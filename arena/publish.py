"""Build a minimal static bundle and publish it to the Hugging Face Space."""

import argparse
import json
import os
import re
from pathlib import Path

from libs.report import benchmark_row, is_single_llm_benchmark


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_DAILY = ROOT / ".cache" / "arena" / "daily.json"
DEFAULT_OPENROUTER = ROOT / ".cache" / "arena" / "openrouter-bench.json"
SPACE = "homerquan/gomoku-arena"
HUB = "https://huggingface.co"
SPACE_FILES = ("README.md", "index.html", "styles.css", "app.js", "board.js", "match-worker.js", "core.py", "roster.json")
ENGINE_FILES = ("LICENSE", "rule.md", "libs/__init__.py", "libs/piece.py", "libs/board.py",
                "libs/ai.py", "libs/eval_fn.py", "libs/llm_player.py")


def deployment_token():
    names = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN")
    for name in names:
        if os.getenv(name):
            return os.environ[name]

    dotdir = Path(os.environ.get("ZDOTDIR") or Path.home())
    for path in (ROOT / ".env", dotdir / ".zshrc"):
        if path.is_file():
            token = re.search(r"""(?m)^\s*(?:export\s+)?HF_TOKEN\s*=\s*["']?(hf_[A-Za-z0-9]+)""", path.read_text())
            if token:
                return token[1]
    from huggingface_hub import get_token

    token = get_token()
    if not token:
        raise SystemExit("Make a Hugging Face write token available as HF_TOKEN.")
    return token


def benchmark_data(openrouter_path=DEFAULT_OPENROUTER):
    runs = {}
    replays = []
    sweeps = {}
    summary_fields = {"wins": "llm_wins", "losses": "ai_wins", "draws": "draws"}
    for path in sorted((ROOT / "benchmarks").glob("*.json")):
        run = json.loads(path.read_text())
        if run.get("kind") == "openrouter_free_benchmark":
            sweeps[run["startedAt"]] = run
            continue
        if not is_single_llm_benchmark(run) or run.get("board_size") != 19 or run.get("ai_level") != "standard":
            continue
        model_name = run.get("model_name", run.get("model"))
        aggregate = runs.setdefault(run.get("model") or model_name,
                                    {"model_name": model_name, "summary": {}, "games": []})
        aggregate["games"].extend(run["games"])
        for field in summary_fields.values():
            aggregate["summary"][field] = aggregate["summary"].get(field, 0) + run["summary"].get(field, 0)
        for game in run["games"]:
            if game.get("termination_reason") != "five_in_a_row":
                continue
            players = {"black": "Alpha-Beta Oracle", "white": "Alpha-Beta Oracle"}
            players[game["llm_color"]] = model_name
            # Publish coordinates and display metadata only.
            moves = [{"color": move["color"], "move": move["move"]} for move in game["moves"]]
            replays.append({"players": players, "moves": moves, "winner": game["winner"], "reason": "five"})

    if openrouter_path and openrouter_path.is_file():
        raw = json.loads(openrouter_path.read_text())
        if "updatedAt" in raw:
            entry = sweeps.get(raw["startedAt"])
            if entry is None or entry["updatedAt"] < raw["updatedAt"]:
                sweeps[raw["startedAt"]] = openrouter_data(raw)
    for sweep in sorted(sweeps.values(), key=lambda report: report["startedAt"]):
        for model in sweep["ranking"]:
            run = runs.setdefault(model["id"], {"summary": {}, "games": []})
            run["model_name"] = model["model"]
            run.setdefault("replays", []).extend(model["games"])
            for score_field, legacy_field in summary_fields.items():
                run["summary"][legacy_field] = run["summary"].get(legacy_field, 0) + model[score_field]
            for game in model["games"]:
                if game["status"] == "complete":
                    color = game["color"]
                    run["games"].append({
                        "winner": game["winner"], "llm_color": color,
                        "moves": [{"player": "llm" if move["color"] == color else "ai"} for move in game["moves"]],
                    })
    ranking = []
    for model_id, run in runs.items():
        row = benchmark_row(run)
        if "replays" in run:
            row["openrouter"] = model_id
            row["replays"] = run["replays"]
        if not (row["wins"] + row["losses"] + row["draws"]):
            row["score"] = None
        ranking.append(row)
    ranking.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["model"]))
    replay = max(replays, key=lambda game: len(game["moves"]), default=None)
    return {"ranking": ranking, "replay": replay,
            "openrouter": {"completedGames": sum(run["completedGames"] for run in sweeps.values()),
                           "plannedGames": sum(run["plannedGames"] for run in sweeps.values())} if sweeps else None}


def openrouter_data(run):
    from arena.core import ArenaGame

    rows = []
    for model in run["models"]:
        summary = dict(llm_wins=0, ai_wins=0, draws=0)
        model_name = model["name"].removesuffix(" (free)").strip()
        games = []
        scored_games = []
        for game in model["games"]:
            color = game["color"]
            if game["status"] != "complete":
                reason = game.get("reason")
                if reason == "OpenRouter/provider HTTP 429":
                    reason = "Free provider rate limited"
                games.append({"color": color, "status": game["status"], "reason": reason})
                continue

            verified = ArenaGame(move["move"] for move in game["moves"]).snapshot()
            if (
                verified["moves"] != game["moves"]
                or verified["winner"] != game["winner"]
                or verified["reason"] != game["reason"]
                or verified["next"] is not None
            ):
                raise ValueError(f"Could not verify {model['id']} as {color}.")
            winner = verified["winner"]
            result = "draws" if winner is None else ("llm_wins" if winner == color else "ai_wins")
            summary[result] += 1
            scored_games.append({
                "winner": winner, "llm_color": color,
                "moves": [{"player": "llm" if move["color"] == color else "ai"} for move in verified["moves"]],
            })
            players = {"black": "Alpha-Beta Oracle", "white": "Alpha-Beta Oracle", color: model_name}
            games.append({
                "color": color, "status": "complete", "players": players,
                "moves": verified["moves"], "winner": winner, "reason": verified["reason"],
            })
        row = benchmark_row({"model": model_name, "summary": summary, "games": scored_games})
        if not scored_games:
            row["score"] = None
        rows.append({**row, "id": model["id"], "games": games, "completedGames": len(scored_games)})

    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), -row["wins"], row["model"]))
    return {
        "kind": "openrouter_free_benchmark",
        "updatedAt": run["updatedAt"], "startedAt": run["startedAt"],
        "verifiedFreeResponses": run["verifiedFreeResponses"],
        "completedGames": sum(row["completedGames"] for row in rows),
        "plannedGames": sum(len(row["games"]) for row in rows),
        "ranking": rows,
    }


def bundle(daily_path, openrouter_path=DEFAULT_OPENROUTER):
    files = {name: (HERE / name).read_bytes() for name in SPACE_FILES}
    files.update({name: (ROOT / name).read_bytes() for name in ENGINE_FILES})
    files["benchmark.json"] = json.dumps(benchmark_data(openrouter_path), ensure_ascii=False).encode()
    if daily_path.is_file():
        files["daily.json"] = daily_path.read_bytes()
    return files


def deploy(files):
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

    hub = HfApi(endpoint=HUB, token=deployment_token())
    if hub.whoami()["name"] != SPACE.split("/")[0]:
        raise SystemExit("The Hugging Face token must belong to the Space owner.")

    try:
        info = hub.space_info(SPACE)
    except RepositoryNotFoundError:
        hub.create_repo(SPACE, repo_type="space", space_sdk="static", private=False)
        info = hub.space_info(SPACE)
    else:
        if info.private or info.sdk != "static":
            raise SystemExit(f"{SPACE} already exists with a different configuration.")
        if "index.html" in {item.rfilename for item in info.siblings}:
            index = hf_hub_download(SPACE, "index.html", repo_type="space", revision=info.sha, token=False, endpoint=HUB)
            if "<title>Gomoku Arena · GomokuBench</title>" not in Path(index).read_text():
                raise SystemExit(f"{SPACE} already serves a different site.")

    try:
        published = hf_hub_download(SPACE, "daily.json", repo_type="space", revision=info.sha, token=False, endpoint=HUB)
    except EntryNotFoundError:
        files.setdefault("daily.json", b"{}\n")
    else:
        def edition_key(data):
            return data.get("day", ""), data.get("generatedAt", "")

        if edition_key(json.loads(files.get("daily.json", b"{}"))) <= edition_key(json.loads(Path(published).read_bytes())):
            files.pop("daily.json", None)

    operations = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=data) for name, data in files.items()]
    if json.loads(files["benchmark.json"]).get("openrouter") and "openrouter.json" in {item.rfilename for item in info.siblings}:
        operations.append(CommitOperationDelete(path_in_repo="openrouter.json"))
    result = hub.create_commit(
        SPACE, repo_type="space", parent_commit=info.sha, commit_message="Update Gomoku Arena standings and bracket",
        operations=operations,
    )
    print(result.commit_url)
    print(f"{HUB}/spaces/{SPACE}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", type=Path, metavar="DIRECTORY")
    mode.add_argument("--publish", action="store_true")
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--openrouter", type=Path, default=DEFAULT_OPENROUTER)
    args = parser.parse_args()
    files = bundle(args.daily, args.openrouter)
    if args.publish:
        deploy(files)
    else:
        files.setdefault("daily.json", b"{}\n")
        for name, data in files.items():
            destination = args.build / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


if __name__ == "__main__":
    main()
