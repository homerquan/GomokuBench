"""Run a Top 16 knockout for rated local and OpenRouter models."""

import argparse
import getpass
import json
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

from arena.core import ArenaGame, SYSTEM
from arena.daily import ModelPair, ROUND_NAMES, update_ratings, write_result
from arena.openrouter_bench import API, MoveUnavailable, NoLegalMove, Router, SweepStopped, choose_router_move, eligible_models
from arena.publish import DEFAULT_DAILY, DEFAULT_OPENROUTER, HERE, openrouter_data
from libs.llm_player import move_to_text


OUTPUT = DEFAULT_DAILY.with_name("openrouter-knockout.json")
FIRST_ROUND = ((0, 7), (3, 4), (1, 6), (2, 5))
NEIGHBORS = ("9,9", "10,9", "11,9", "9,10", "11,10", "9,11", "10,11", "11,11")


class Knockout:
    def __init__(self, output, base, local_roster, challengers):
        self.output = output
        self.base = base
        self.local_roster = local_roster
        self.lock = threading.Lock()
        self.incumbents = [row["id"] for row in base["ranking"] if row["id"] in local_roster][:8]
        self.challengers = [row["id"] for row in challengers][:8]
        if len(self.incumbents) != 8 or len(self.challengers) != 8:
            raise SystemExit("This edition needs eight ranked incumbents and eight ranked challengers.")

        if output.is_file():
            self.data = json.loads(output.read_text())
            if (self.data["priorGeneratedAt"], self.data["seeds"]) != (
                base["generatedAt"], self.incumbents + self.challengers
            ):
                raise SystemExit("The saved bracket uses a different field or baseline. Supply a new output path.")
            if self.data["status"] == "paused":
                self.data["status"] = "running"
                self.save()
        else:
            day = datetime.now(timezone.utc).date().isoformat()
            self.data = {
                "version": 1,
                "day": day,
                "edition": "OpenRouter Challenge",
                "gamesPerFixture": 1,
                "priorGeneratedAt": base["generatedAt"],
                "seeds": self.incumbents + self.challengers,
                "entrants": {**base.get("entrants", {}), **{row["id"]: row["model"] for row in challengers}},
                "rounds": [{"name": name, "matches": [None] * (8 >> i)} for i, name in enumerate(ROUND_NAMES)],
                "ranking": base["ranking"],
                "champion": None,
                "status": "running",
            }
            for slot, (higher, lower) in enumerate(FIRST_ROUND):
                for group, entrants in enumerate((self.incumbents, self.challengers)):
                    self.data["rounds"][0]["matches"][slot * 2 + group] = self.fixture(
                        [entrants[higher], entrants[lower]], 0, slot * 2 + group,
                    )
            self.save()

    def fixture(self, pair, stage, slot):
        rng = random.Random(f"{self.data['day']}-{stage}-{slot}")
        black, white = pair if (slot // 2 + stage) % 2 == 0 else pair[::-1]
        game = ArenaGame(("10,10", rng.choice(NEIGHBORS)))
        return {"players": pair, "score": [None, None], "winner": None, "status": "queued",
                "games": [{**game.snapshot(), "black": black, "white": white, "opening": 2}]}

    def save(self):
        self.data["generatedAt"] = datetime.now(timezone.utc).isoformat()
        write_result(self.output, self.data)

    def update(self, fixture, game, **changes):
        with self.lock:
            fixture["games"][0].update({**game, "moves": game["moves"].copy()})
            fixture.update(changes)
            if fixture["status"] == "complete":
                self.update_ranking()
            self.save()

    def update_ranking(self):
        ratings = {
            row["id"]: {key: row[key] for key in ("elo", "wins", "losses", "draws")}
            for row in self.base["ranking"]
        }
        for model_id in self.challengers:
            ratings.setdefault(model_id, {"elo": 1500, "wins": 0, "losses": 0, "draws": 0})
        for match in self.data.get("placementMatches", []):
            for player in match["players"]:
                ratings.setdefault(player, {"elo": 1500, "wins": 0, "losses": 0, "draws": 0})
        matches = [match for stage in self.data["rounds"] for match in stage["matches"]]
        for match in matches + self.data.get("placementMatches", []):
            if match and match["status"] == "complete" and match["decidedBy"] != "forfeit":
                update_ratings(ratings, match["games"])
        ranked = [{"id": key, **value, "elo": round(value["elo"], 4)} for key, value in ratings.items()
                  if value["wins"] + value["losses"] + value["draws"]]
        ranked.sort(key=lambda row: (-row["elo"], -row["wins"], row["id"]))
        self.data["ranking"] = ranked

    def stage(self, index):
        matches = self.data["rounds"][index]["matches"]
        if index > 0:
            previous = self.data["rounds"][index - 1]["matches"]
            for slot, match in enumerate(matches):
                if match:
                    continue
                feeders = previous[2 * slot:2 * slot + 2]
                if all(feeder and feeder["winner"] for feeder in feeders):
                    matches[slot] = self.fixture([feeder["winner"] for feeder in feeders], index, slot)
            self.save()
        return [match for match in matches if match and match["status"] != "complete"]

    def finish(self):
        final = self.data["rounds"][-1]["matches"][0]
        self.data["champion"] = final["winner"] if final else None
        self.data["status"] = "complete" if self.data["champion"] else "paused"
        self.save()

    def placement_games(self, models):
        rated = {row["id"] for row in self.data["ranking"]}
        final = self.data["rounds"][-1]["matches"][0]
        finalists = set(final["players"]) if final and final["status"] != "complete" else set()
        opponents = [model_id for model_id in self.challengers if model_id in rated and model_id not in finalists]
        opponents = opponents or self.incumbents
        matches = self.data.setdefault("placementMatches", [])
        fixtures = {match["players"][0] for match in matches}
        for model in models:
            model_id = model["id"]
            self.data["entrants"][model_id] = model["model"]
            if model_id in rated or model_id in fixtures:
                continue
            slot = len(matches)
            match = self.fixture([model_id, opponents[slot % len(opponents)]], len(ROUND_NAMES), slot)
            match["placement"] = True
            matches.append(match)
        self.save()
        return [match for match in matches if match["status"] != "complete"]


def hosted_move(game, model_id, router):
    moves = game.candidate_moves()
    prompt = game.choice_prompt(moves)
    move = choose_router_move(
        router, model_id, game.state, SYSTEM,
        lambda attempt, error: prompt if error is None else f"{prompt}\n{error} Reply with one x,y coordinate.",
        allowed=set(moves),
    )
    return move_to_text(move)


def play_fixture(knockout, fixture, router):
    pair = fixture["players"]
    replay = fixture["games"][0]
    game = ArenaGame(move["move"] for move in replay["moves"])
    local = knockout.local_roster
    native = ModelPair(threads=2, gpu_layers=0)
    knockout.update(fixture, game.snapshot(), status="playing", error=None)
    print(f"{pair[0]} vs {pair[1]} · resuming at {len(game.moves)} moves", flush=True)
    decided_by = "five"
    try:
        native.retain([local[model_id] for model_id in pair if model_id in local])
        while (snapshot := game.snapshot())["next"]:
            color = snapshot["next"]
            model_id = replay[color]
            try:
                move = native.move(local[model_id], game) if model_id in local else hosted_move(game, model_id, router)
            except NoLegalMove:
                winner = "white" if color == "black" else "black"
                snapshot = {**snapshot, "winner": winner, "reason": "forfeit", "next": None}
                decided_by = "forfeit"
                break
            game.apply(move)
            knockout.update(fixture, game.snapshot())
    except (MoveUnavailable, SweepStopped) as error:
        knockout.update(fixture, game.snapshot(), status="blocked", error=str(error))
        print(f"{pair[0]} vs {pair[1]} · paused after {len(game.moves)} moves · {error}", flush=True)
        return
    finally:
        native.close()

    if snapshot["winner"]:
        winner = replay[snapshot["winner"]]
        score = [int(model_id == winner) for model_id in pair]
    else:
        winner = None if fixture.get("placement") else min(pair, key=knockout.data["seeds"].index)
        score = [0.5, 0.5]
        decided_by = "draw" if fixture.get("placement") else "higher seed"
    knockout.update(fixture, snapshot, status="complete", score=score, winner=winner, decidedBy=decided_by)
    outcome = winner or "draw"
    print(f"{pair[0]} vs {pair[1]} · {outcome} after {len(game.moves)} moves · {decided_by}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--oracle-results", type=Path, default=DEFAULT_OPENROUTER)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--placement-games", action="store_true", help="Try rating games for the remaining OpenRouter entrants.")
    args = parser.parse_args()
    key = getpass.getpass("OpenRouter credential (hidden input): ")
    if not re.fullmatch(r"sk-or-v1-[a-f0-9]{64}", key):
        raise SystemExit("Invalid OpenRouter key format.")

    previous = json.loads(args.base.read_text())
    local = {model["id"]: model for model in json.loads((HERE / "roster.json").read_text())}
    saved_benchmark = json.loads(args.oracle_results.read_text())
    benchmark = saved_benchmark if saved_benchmark.get("kind") == "openrouter_free_benchmark" else openrouter_data(saved_benchmark)
    challengers = [row for row in benchmark["ranking"] if row["completedGames"] == 2]
    with httpx.Client(base_url=API, headers={"Authorization": "Bearer " + key},
                      follow_redirects=False, timeout=httpx.Timeout(80, connect=12)) as client:
        response = client.get("models", params={"input_modalities": "text", "output_modalities": "text"})
        response.raise_for_status()
        router = Router(client, eligible_models(response.json()["data"]))
        print(f"{router.limit} free requests available.", flush=True)
        knockout = Knockout(args.output, previous, local, challengers)
        with ThreadPoolExecutor(max_workers=6) as pool:
            for stage in range(len(ROUND_NAMES)):
                print(ROUND_NAMES[stage], flush=True)
                matches = knockout.stage(stage)
                if args.placement_games and stage == len(ROUND_NAMES) - 1:
                    matches += knockout.placement_games(benchmark["ranking"])
                for job in [pool.submit(play_fixture, knockout, match, router) for match in matches]:
                    job.result()
        knockout.finish()
        print(f"{router.verified} responses confirmed $0 across {router.attempts} attempts.", flush=True)
        print(f"Knockout status: {knockout.data['status']}. Results at {args.output}.", flush=True)


if __name__ == "__main__":
    main()
