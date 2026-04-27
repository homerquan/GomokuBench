import argparse

from libs import piece
from libs.ai import AI_LEVELS
from libs.benchmark import BenchmarkLLMCallError, run_benchmark
from libs.dual import DualLLMCallError, run_dual
from libs.game import GameSession
from libs.progress import BenchmarkProgress
from libs.report import generate_report


def run_cli(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "play":
        return run_play(args)
    if args.command == "benchmark":
        return run_benchmark_command(args)
    if args.command == "dual":
        return run_dual_command(args)
    if args.command == "report":
        generate_report()
        return 0

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
    play_parser.add_argument(
        "--ai-level",
        choices=tuple(AI_LEVELS.keys()),
        default="standard",
        help="AI strength level. Default: standard",
    )

    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark an LLM against the AI.")
    model_group = benchmark_parser.add_mutually_exclusive_group(required=True)
    model_group.add_argument("--model", help="Model config name in the models folder.")
    model_group.add_argument("--model-file", help="Path to a custom model JSON config file.")
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
    benchmark_parser.add_argument(
        "--ai-level",
        choices=tuple(AI_LEVELS.keys()),
        default="standard",
        help="AI strength level. Default: standard",
    )

    dual_parser = subparsers.add_parser("dual", help="Run a Black LLM vs White LLM game.")
    dual_parser.add_argument(
        "--BLACK-LLM-FILE",
        "--black-llm-file",
        dest="black_llm_file",
        required=True,
        help="Path to the Black LLM model config JSON file.",
    )
    dual_parser.add_argument(
        "--WHITE-LLM-FILE",
        "--white-llm-file",
        dest="white_llm_file",
        required=True,
        help="Path to the White LLM model config JSON file.",
    )
    dual_parser.add_argument(
        "-r",
        "--rounds",
        type=int,
        default=1,
        help="How many complete LLM-vs-LLM games to play. Default: 1",
    )
    dual_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each round, move, and board state in the console.",
    )
    dual_parser.add_argument(
        "--debug-http",
        action="store_true",
        help="Print HTTP error details from an LLM provider when a request fails.",
    )
    subparsers.add_parser("report", help="Report benchmark results.")
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
    game = GameSession(human_color=human_color, ai_level=args.ai_level)

    print(f"You are {color_name(game.human_color)} ({piece.SYMBOLS[game.human_color]}).")
    print(f"AI is {color_name(game.ai_color)} ({piece.SYMBOLS[game.ai_color]}).")
    print(f"AI level: {game.ai_level}.")
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
            start_callback=lambda runner: print(
                f"LLM reasoning process log in: {runner.reasoning_log_path}"
            ),
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


def run_dual_command(args):
    try:
        output_path, report = run_dual(
            args,
            start_callback=lambda runner: print(
                "LLM reasoning process logs in: "
                f"black={runner.black_reasoning_log_path}, "
                f"white={runner.white_reasoning_log_path}"
            ),
        )
    except DualLLMCallError as error:
        print(error)
        print("Dual game was not saved.")
        return 1
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(error)
        return 1

    print(f"Saved dual game to {output_path}")
    summary = report["summary"]
    print(
        f"Black wins: {summary['black_wins']}, "
        f"White wins: {summary['white_wins']}, "
        f"Draws: {summary['draws']}"
    )
    if report["rounds"] == 1:
        print(f"Winner: {report['winner']}. Reason: {report['termination_reason']}.")
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
