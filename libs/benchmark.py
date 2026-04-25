import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from libs import piece
from libs.game import GameSession
from libs.llm_player import LLMMoveError, LLMPlayer, LLMRequestError
from libs.model_config import load_model_config


BENCHMARK_DIR = Path.cwd() / "benchmarks"


@dataclass
class BenchmarkSummary:
    llm_wins: int = 0
    ai_wins: int = 0
    draws: int = 0


class BenchmarkLLMCallError(RuntimeError):
    pass


class BenchmarkRunner:
    def __init__(
        self,
        model_config,
        rounds,
        ai_level="standard",
        llm_player=None,
        progress_callback=None,
        verbose=False,
        stream=None,
        debug_http=False,
    ):
        self.model_config = model_config
        self.rounds = rounds
        self.size = 19
        self.ai_level = ai_level
        self.llm_player = llm_player or LLMPlayer(model_config, debug_http=debug_http)
        self.progress_callback = progress_callback
        self.verbose = verbose
        self.stream = stream or sys.stdout

    def run(self):
        results = []
        summary = BenchmarkSummary()
        completed_rounds = 0

        ai_first_rounds = self.rounds // 2
        llm_first_rounds = self.rounds - ai_first_rounds

        self._report_progress(completed_rounds)

        for round_number in range(1, ai_first_rounds + 1):
            result = self._play_round(round_number=round_number, ai_first=True)
            results.append(result)
            update_summary(summary, result["outcome"])
            completed_rounds += 1
            self._report_progress(completed_rounds)

        for round_offset in range(1, llm_first_rounds + 1):
            result = self._play_round(
                round_number=ai_first_rounds + round_offset,
                ai_first=False,
            )
            results.append(result)
            update_summary(summary, result["outcome"])
            completed_rounds += 1
            self._report_progress(completed_rounds)

        report = {
            "model": self.model_config.model_id,
            "model_name": self.model_config.display_name,
            "provider": self.model_config.provider_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rounds": self.rounds,
            "board_size": self.size,
            "ai_level": self.ai_level,
            "summary": {
                "llm_wins": summary.llm_wins,
                "ai_wins": summary.ai_wins,
                "draws": summary.draws,
                "ai_first_rounds": ai_first_rounds,
                "llm_first_rounds": llm_first_rounds,
            },
            "games": results,
        }
        return report

    def _report_progress(self, completed_rounds):
        if self.progress_callback:
            self.progress_callback(completed_rounds, self.rounds)

    def _log(self, message="", board=None):
        if not self.verbose:
            return

        self.stream.write("\n")
        if message:
            self.stream.write(f"{message}\n")
        if board is not None:
            self.stream.write(f"{board}\n")
        self.stream.flush()

    def _play_round(self, round_number, ai_first):
        llm_color = piece.WHITE if ai_first else piece.BLACK
        session = GameSession(size=self.size, human_color=llm_color, ai_level=self.ai_level)
        moves = []
        termination_reason = None

        self._log(
            (
                f"Round {round_number}/{self.rounds} started. "
                f"AI is {color_name(session.ai_color)}, LLM is {color_name(llm_color)}. "
                f"{'AI' if ai_first else 'LLM'} moves first. "
                f"AI level: {session.ai_level}."
            ),
            board=session.state,
        )

        while not session.finished:
            if session.human_to_move():
                try:
                    move, response_text = self.llm_player.choose_move(session, llm_color)
                except LLMRequestError as error_message:
                    raise BenchmarkLLMCallError(
                        f"Error calling LLM in round {round_number}: {error_message}"
                    ) from error_message
                except LLMMoveError as error_message:
                    termination_reason = "llm_forfeit"
                    winner = color_name(session.ai_color)
                    return {
                        "round": round_number,
                        "ai_first": ai_first,
                        "llm_color": color_name(llm_color),
                        "ai_color": color_name(session.ai_color),
                        "outcome": "ai_win",
                        "winner": winner,
                        "termination_reason": termination_reason,
                        "error": str(error_message),
                        "moves": moves,
                        "final_board": str(session.state),
                    }

                session.play_human(*move)
                self._log(
                    f"LLM ({color_name(llm_color)}) plays {move_to_text(move)}.",
                    board=session.state,
                )
                moves.append(
                    {
                        "player": "llm",
                        "color": color_name(llm_color),
                        "move": move_to_text(move),
                        "response": response_text,
                    }
                )
                continue

            move = session.play_ai()
            if move is None:
                termination_reason = "ai_could_not_move"
                self._log("AI could not make a move.", board=session.state)
                return {
                    "round": round_number,
                    "ai_first": ai_first,
                    "llm_color": color_name(llm_color),
                    "ai_color": color_name(session.ai_color),
                    "outcome": "draw",
                    "winner": "none",
                    "termination_reason": termination_reason,
                    "moves": moves,
                    "final_board": str(session.state),
                }

            self._log(
                f"AI ({color_name(session.ai_color)}) plays {move_to_text(move)}.",
                board=session.state,
            )
            moves.append(
                {
                    "player": "ai",
                    "color": color_name(session.ai_color),
                    "move": move_to_text(move),
                }
            )

        if session.winner == llm_color:
            outcome = "llm_win"
            winner = color_name(llm_color)
        elif session.winner == session.ai_color:
            outcome = "ai_win"
            winner = color_name(session.ai_color)
        else:
            outcome = "draw"
            winner = "none"

        termination_reason = "five_in_a_row" if session.winner != piece.EMPTY else "board_full"
        self._log(
            (
                f"Round {round_number} finished. "
                f"Winner: {winner}. "
                f"Reason: {termination_reason}."
            ),
            board=session.state,
        )
        return {
            "round": round_number,
            "ai_first": ai_first,
            "llm_color": color_name(llm_color),
            "ai_color": color_name(session.ai_color),
            "outcome": outcome,
            "winner": winner,
            "termination_reason": termination_reason,
            "moves": moves,
            "final_board": str(session.state),
        }


def run_benchmark(args, progress_callback=None):
    if args.rounds <= 0:
        raise ValueError("Rounds must be a positive integer.")

    model_config = load_model_config(
        getattr(args, "model", None),
        model_file=getattr(args, "model_file", None),
    )
    runner = BenchmarkRunner(
        model_config=model_config,
        rounds=args.rounds,
        ai_level=getattr(args, "ai_level", "standard"),
        progress_callback=progress_callback,
        verbose=getattr(args, "verbose", False),
        debug_http=getattr(args, "debug_http", False),
    )
    report = runner.run()
    output_path = save_report(model_config.config_name, report)
    return output_path, report


def save_report(model_name, report):
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BENCHMARK_DIR / f"{model_name}.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    return output_path


def move_to_text(move):
    row, column = move
    return f"{column + 1},{row + 1}"


def color_name(color):
    return "black" if color == piece.BLACK else "white"


def update_summary(summary, outcome):
    if outcome == "llm_win":
        summary.llm_wins += 1
    elif outcome == "ai_win":
        summary.ai_wins += 1
    else:
        summary.draws += 1
