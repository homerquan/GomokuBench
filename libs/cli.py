import argparse

from libs import piece
from libs.benchmark import BenchmarkLLMCallError, run_benchmark
from libs.game import GameSession
from libs.progress import BenchmarkProgress


def run_cli(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "play":
        return run_play(args)
    if args.command == "benchmark":
        return run_benchmark_command(args)

    parser.print_help()
    return 1


def build_parser():
    parser = argparse.ArgumentParser(description="Play Gomoku from the command line.")
    subparsers = parser.add_subparsers(dest="command")

    play_parser = subparsers.add_parser("play", help="Start a Gomoku game.")
    play_parser.add_argument(
        "--player",
        choices=("black", "white"),
        default=None,
        help="Your stone color. If omitted, the first mover is black.",
    )
    play_parser.add_argument(
        "--ai-first",
        action="store_true",
        help="Let the AI make the first move.",
    )

    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark an LLM against the AI.")
    benchmark_parser.add_argument("--model", required=True, help="Model config name in the models folder.")
    benchmark_parser.add_argument(
        "-r",
        "--rounds",
        type=int,
        default=10,
        help="How many rounds to play. Default: 10",
    )
    benchmark_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each round and move in the console while benchmarking.",
    )
    benchmark_parser.add_argument(
        "--debug-http",
        action="store_true",
        help="Print HTTP error details from the LLM provider when a benchmark request fails.",
    )
    return parser


def run_play(args):
    if args.ai_first and args.player == "black":
        print("`--ai-first` cannot be used with `--player black` because black moves first.")
        return 1

    if args.ai_first:
        human_color = piece.WHITE
    elif args.player == "white":
        human_color = piece.WHITE
    else:
        human_color = piece.BLACK
    game = GameSession(human_color=human_color)

    print(f"You are {color_name(game.human_color)} ({piece.SYMBOLS[game.human_color]}).")
    print(f"AI is {color_name(game.ai_color)} ({piece.SYMBOLS[game.ai_color]}).")
    print("Enter moves as x,y using 1-based coordinates. Example: 10,10")
    print("Type 'q' to quit.")

    while True:
        print()
        print(game.state)

        if game.finished:
            print()
            print(result_message(game))
            return 0

        if game.human_to_move():
            try:
                raw_move = input("\nWhat's your move? (x,y): ").strip()
            except EOFError:
                print("\nGame ended.")
                return 0
            if raw_move.lower() in {"q", "quit", "exit"}:
                print("Game ended.")
                return 0

            try:
                row, column = parse_move(raw_move, game.size)
            except ValueError as error:
                print(error)
                continue

            if not game.play_human(row, column):
                print("That spot is not available.")
                continue
            continue

        move = game.play_ai()
        if move is None:
            print("AI could not make a move.")
            return 1
        print(f"\nAI plays {move[1] + 1},{move[0] + 1}. What's your move? (x,y)")


def run_benchmark_command(args):
    progress = BenchmarkProgress(args.rounds)
    try:
        output_path, report = run_benchmark(
            args,
            progress_callback=lambda completed, total: progress.update(completed),
        )
    except BenchmarkLLMCallError as error:
        progress.newline()
        print(error)
        print("Benchmark was not saved.")
        return 1
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        progress.newline()
        print(error)
        return 1
    progress.finish()

    summary = report["summary"]
    print(f"Saved benchmark to {output_path}")
    print(
        f"LLM wins: {summary['llm_wins']}, "
        f"AI wins: {summary['ai_wins']}, "
        f"Draws: {summary['draws']}"
    )
    return 0


def parse_move(raw_move, size):
    parts = [part.strip() for part in raw_move.split(",")]
    if len(parts) != 2:
        raise ValueError("Enter your move as x,y. Example: 10,10")

    try:
        x_value = int(parts[0])
        y_value = int(parts[1])
    except ValueError as error:
        raise ValueError("Coordinates must be integers.") from error

    if not (1 <= x_value <= size and 1 <= y_value <= size):
        raise ValueError(f"Coordinates must be between 1 and {size}.")

    return y_value - 1, x_value - 1


def result_message(game):
    if game.winner == piece.EMPTY:
        return "It's a draw."
    if game.winner == game.human_color:
        return "You win with five in a row."
    return "You lose. AI made five in a row."


def color_name(color):
    return "black" if color == piece.BLACK else "white"
