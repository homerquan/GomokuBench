import json
from pathlib import Path


def generate_report():
    benchmarks_dir = Path("benchmarks")
    if not benchmarks_dir.exists():
        print("No benchmarks folder found.")
        return

    reports = []
    for benchmark_file in benchmarks_dir.glob("*.json"):
        with open(benchmark_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not is_single_llm_benchmark(data):
            continue

        summary = data["summary"]
        games = data["games"]

        total_wins = summary.get("llm_wins", 0)
        total_losses = summary.get("ai_wins", 0)
        total_draws = summary.get("draws", 0)

        llm_win_moves = []
        llm_loss_moves = []

        for game in games:
            moves = game.get("moves", [])
            llm_moves_count = len([m for m in moves if m.get("player") == "llm"])

            winner = game.get("winner")
            llm_color = game.get("llm_color")

            if winner == llm_color:
                llm_win_moves.append(llm_moves_count)
            elif winner and winner != llm_color:
                llm_loss_moves.append(llm_moves_count)

        avg_win_moves = sum(llm_win_moves) / len(llm_win_moves) if llm_win_moves else 0
        avg_loss_moves = sum(llm_loss_moves) / len(llm_loss_moves) if llm_loss_moves else 0

        # Simple scoring evaluation
        # Winner score: higher win rate + lower average moves to win
        # Loser score: lower loss rate + higher average moves to lose (better defense)

        total_rounds = total_wins + total_losses + total_draws
        win_rate = total_wins / total_rounds if total_rounds > 0 else 0

        score = (win_rate * 100) - (avg_win_moves * 0.5) + (avg_loss_moves * 0.2)

        reports.append({
            "model": data.get("model_name", data.get("model")),
            "wins": total_wins,
            "losses": total_losses,
            "draws": total_draws,
            "avg_win_moves": round(avg_win_moves, 2),
            "avg_loss_moves": round(avg_loss_moves, 2),
            "score": round(score, 2)
        })

    if not reports:
        print("No benchmark result files found.")
        return

    # Sort by score
    reports.sort(key=lambda x: x["score"], reverse=True)

    # Print markdown table
    print("| Model | Wins | Losses | Draws | Avg Moves (Win) | Avg Moves (Loss) | Score |")
    print("|---|---|---|---|---|---|---|")
    for r in reports:
        print(f"| {r['model']} | {r['wins']} | {r['losses']} | {r['draws']} | {r['avg_win_moves']} | {r['avg_loss_moves']} | {r['score']} |")


def is_single_llm_benchmark(data):
    if data.get("mode") == "dual":
        return False

    summary = data.get("summary")
    games = data.get("games")
    if not isinstance(summary, dict) or not isinstance(games, list):
        return False

    return {"llm_wins", "ai_wins", "draws"}.issubset(summary)
