"""Play and optionally publish one dated Top 16 tournament."""

import argparse
import gc
import json
import os
import random
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from arena.core import ArenaGame
from arena.publish import DEFAULT_DAILY, HUB, SPACE, deployment_token


HERE = Path(__file__).resolve().parent
SEED_ORDER = (1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11)
ROUND_NAMES = ("Round of 16", "Quarterfinals", "Semifinals", "Final")


class ModelPair:
    def __init__(self, threads, gpu_layers):
        self.threads = threads
        self.gpu_layers = gpu_layers
        self.models = {}

    def load(self, model):
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        path = hf_hub_download(
            repo_id=model["repo"], filename=model["file"],
            revision=model["revision"], token=False, endpoint=HUB,
        )
        self.models[model["id"]] = Llama(
            model_path=path, n_ctx=2048, n_threads=self.threads,
            n_gpu_layers=self.gpu_layers, verbose=False,
        )

    def move(self, model, game):
        from llama_cpp import LlamaGrammar

        request = game.request(model.get("template", "chatml"))
        result = self.models[model["id"]].create_completion(
            request["prompt"], max_tokens=12, temperature=0,
            grammar=LlamaGrammar.from_string(request["grammar"], verbose=False),
        )
        return result["choices"][0]["text"]

    def retain(self, pair):
        ids = {model["id"] for model in pair}
        for model_id in self.models.keys() - ids:
            self.models.pop(model_id).close()
        gc.collect()
        for model in pair:
            if model["id"] not in self.models:
                self.load(model)

    def close(self):
        self.retain([])


def play_game(pair, opening, engine):
    game = ArenaGame(opening)
    models = dict(zip(("black", "white"), pair))
    while (snapshot := game.snapshot())["next"]:
        model = models[snapshot["next"]]
        game.apply(engine.move(model, game))
    return {**snapshot, "black": pair[0]["id"], "white": pair[1]["id"], "opening": len(opening)}


def fixture(pair, opening, engine):
    engine.retain(pair)
    games = [play_game(colors, opening, engine) for colors in (pair, pair[::-1])]
    ids = [model["id"] for model in pair]
    points = dict.fromkeys(ids, 0)
    win_lengths = {}
    for game in games:
        if game["winner"]:
            winner = game[game["winner"]]
            points[winner] += 1
            win_lengths[winner] = len(game["moves"])
        else:
            for model_id in ids:
                points[model_id] += 0.5
    if points[ids[0]] != points[ids[1]]:
        winner = max(ids, key=points.get)
        decided_by = "points"
    elif len(win_lengths) == 2 and len(set(win_lengths.values())) == 2:
        winner = min(ids, key=win_lengths.get)
        decided_by = "faster win"
    else:
        winner = min(pair, key=lambda model: model["seed"])["id"]
        decided_by = "higher seed"
    return {"players": ids, "winner": winner, "score": [points[i] for i in ids],
            "decidedBy": decided_by, "games": games}


def update_ratings(ratings, games):
    for game in games:
        black, white = (ratings[game[color]] for color in ("black", "white"))
        chance = 1 / (1 + 10 ** ((white["elo"] - black["elo"]) / 400))
        result = {"black": (1, "wins", "losses"), "white": (0, "losses", "wins"),
                  None: (0.5, "draws", "draws")}[game["winner"]]
        adjustment = 24 * (result[0] - chance)
        black["elo"] += adjustment
        white["elo"] -= adjustment
        black[result[1]] += 1
        white[result[2]] += 1


def tournament(day, roster, previous, engine):
    prior = {record["id"]: record for record in previous.get("ranking", [])}
    rated_ids = dict.fromkeys((*prior, *(model["id"] for model in roster)))
    ratings = {model_id: {key: prior.get(model_id, {}).get(key, value)
                              for key, value in (("elo", 1500), ("wins", 0), ("losses", 0), ("draws", 0))}
               for model_id in rated_ids}
    rng = random.Random(day)
    seeds = list(roster)
    rng.shuffle(seeds)
    prior_places = {model_id: rank for rank, model_id in enumerate(prior)}
    seeds.sort(key=lambda model: prior_places.get(model["id"], len(prior_places)))
    seeded = {model["id"]: {**model, "seed": i} for i, model in enumerate(seeds, 1)}
    entrants = [seeded[seeds[i - 1]["id"]] for i in SEED_ORDER]
    rounds = []
    for name in ROUND_NAMES:
        matches = []
        for offset in range(0, len(entrants), 2):
            pair = entrants[offset:offset + 2]
            neighbors = ("9,9", "10,9", "11,9", "9,10", "11,10", "9,11", "10,11", "11,11")
            result = fixture(pair, ("10,10", rng.choice(neighbors)), engine)
            matches.append(result)
            update_ratings(ratings, result["games"])
            print(f"{name} {offset // 2 + 1}: {' vs '.join(result['players'])} "
                  f"{result['score']} -> {result['winner']}", flush=True)
        rounds.append({"name": name, "matches": matches})
        entrants = [seeded[result["winner"]] for result in matches]

    ranking = [{"id": model_id, **score, "elo": round(score["elo"], 4)}
               for model_id, score in ratings.items()]
    ranking.sort(key=lambda record: (-record["elo"], -record["wins"], record["id"]))
    return {
        "version": 1, "day": day, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "priorGeneratedAt": previous.get("generatedAt"),
        "champion": entrants[0]["id"], "seeds": [model["id"] for model in seeds],
        "rounds": rounds, "ranking": ranking, "entrants": previous.get("entrants", {}),
    }


def write_result(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        json.dump(data, output, separators=(",", ":"))
        output.write("\n")
    Path(output.name).replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument("--output", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--threads", type=int, default=min(os.cpu_count() or 2, 6))
    parser.add_argument("--gpu-layers", type=int, default=0)
    parser.add_argument("--publish", action="store_true", help="Update the published Hugging Face Space.")
    args = parser.parse_args()
    day = args.day.isoformat()
    cached = json.loads(args.output.read_text()) if args.output.is_file() else {}
    previous = cached
    parent = None
    if args.publish:
        from huggingface_hub import HfApi, hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError
        hub = HfApi(endpoint=HUB, token=deployment_token())
        parent = hub.space_info(SPACE).sha
        try:
            remote = hf_hub_download(SPACE, "daily.json", repo_type="space", revision=parent, token=False, endpoint=HUB)
            previous = json.loads(Path(remote).read_text())
        except EntryNotFoundError:
            previous = {}

    if (previous.get("day") or "") >= day:
        print(f"Latest tournament is already {previous['day']}.")
        return
    if cached.get("day") == day and cached.get("priorGeneratedAt") == previous.get("generatedAt"):
        result = cached
    else:
        roster = json.loads((HERE / "roster.json").read_text())
        engine = ModelPair(args.threads, args.gpu_layers)
        try:
            result = tournament(day, roster, previous, engine)
        finally:
            engine.close()
        write_result(args.output, result)
    if args.publish:
        hub.upload_file(
            path_or_fileobj=args.output, path_in_repo="daily.json", repo_id=SPACE,
            repo_type="space", parent_commit=parent, commit_message=f"Top 16 tournament for {day}",
        )


if __name__ == "__main__":
    main()
