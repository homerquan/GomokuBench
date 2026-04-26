import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from libs import piece
from libs.board import BoardState
from libs.llm_player import LLMMoveError, LLMPlayer, LLMRequestError
from libs.model_config import load_model_config


DUAL_REPORT_DIR = Path.cwd() / "benchmarks"
DUAL_LOG_DIR = Path("/tmp/gomokubench")


class DualLLMCallError(RuntimeError):
    pass


@dataclass
class DualSummary:
    black_wins: int = 0
    white_wins: int = 0
    draws: int = 0


@dataclass
class DualGameView:
    state: BoardState

    @property
    def size(self):
        return self.state.size


class DualRunner:
    def __init__(
        self,
        black_config,
        white_config,
        black_player=None,
        white_player=None,
        size=19,
        rounds=1,
        verbose=False,
        stream=None,
        debug_http=False,
        run_id=None,
    ):
        self.black_config = black_config
        self.white_config = white_config
        self.size = size
        self.rounds = rounds
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.run_id = run_id or uuid4().hex
        self.black_reasoning_log_path = DUAL_LOG_DIR / f"{self.run_id}-black.log"
        self.white_reasoning_log_path = DUAL_LOG_DIR / f"{self.run_id}-white.log"
        self.black_player = black_player or LLMPlayer(
            black_config,
            debug_http=debug_http,
            reasoning_log_path=self.black_reasoning_log_path,
        )
        self.white_player = white_player or LLMPlayer(
            white_config,
            debug_http=debug_http,
            reasoning_log_path=self.white_reasoning_log_path,
        )

    def run(self):
        games = []
        summary = DualSummary()

        for round_number in range(1, self.rounds + 1):
            game = self._play_game(round_number)
            games.append(game)
            update_summary(summary, game["outcome"])

        report = {
            "mode": "dual",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "rounds": self.rounds,
            "board_size": self.size,
            "black": {
                "model": self.black_config.model_id,
                "model_name": self.black_config.display_name,
                "provider": self.black_config.provider_name,
                "reasoning_log": str(self.black_reasoning_log_path),
            },
            "white": {
                "model": self.white_config.model_id,
                "model_name": self.white_config.display_name,
                "provider": self.white_config.provider_name,
                "reasoning_log": str(self.white_reasoning_log_path),
            },
            "summary": {
                "black_wins": summary.black_wins,
                "white_wins": summary.white_wins,
                "draws": summary.draws,
            },
            "games": games,
        }

        if len(games) == 1:
            report.update(
                {
                    "outcome": games[0]["outcome"],
                    "winner": games[0]["winner"],
                    "termination_reason": games[0]["termination_reason"],
                    "moves": games[0]["moves"],
                    "final_board": games[0]["final_board"],
                }
            )
            if "error" in games[0]:
                report["error"] = games[0]["error"]

        return report

    def _play_game(self, round_number):
        first_color = self._first_color_for_round(round_number)
        state = BoardState(size=self.size, color=-first_color)
        moves = []
        termination_reason = None

        self._log(
            (
                f"Dual LLM game {round_number}/{self.rounds} started. "
                f"{color_name(first_color).capitalize()} moves first. "
                f"Black: {self.black_config.display_name}. "
                f"White: {self.white_config.display_name}."
            ),
            board=state,
        )

        while not state.is_terminal():
            color = -state.color
            player = self._player_for_color(color)
            config = self._config_for_color(color)
            color_text = color_name(color)
            game_view = DualGameView(state=state)

            try:
                move, response_text = player.choose_move(game_view, color)
            except LLMRequestError as error_message:
                raise DualLLMCallError(
                    f"Error calling {color_text} LLM: {error_message}"
                ) from error_message
            except LLMMoveError as error_message:
                termination_reason = f"{color_text}_llm_forfeit"
                winner_color = -color
                return self._build_game_report(
                    round_number=round_number,
                    first_color=first_color,
                    moves=moves,
                    state=state,
                    outcome=f"{color_name(winner_color)}_win",
                    winner=color_name(winner_color),
                    termination_reason=termination_reason,
                    error=str(error_message),
                )

            state = state.next(move)
            move_text = move_to_text(move)
            self._log(
                (
                    f"{color_text.capitalize()} LLM "
                    f"({config.display_name}) plays {move_text}."
                ),
                board=state,
            )
            moves.append(
                {
                    "player": f"{color_text}_llm",
                    "color": color_text,
                    "model": config.model_id,
                    "model_name": config.display_name,
                    "move": move_text,
                    "response": response_text,
                }
            )

        if state.winner == piece.BLACK:
            outcome = "black_win"
            winner = "black"
        elif state.winner == piece.WHITE:
            outcome = "white_win"
            winner = "white"
        else:
            outcome = "draw"
            winner = "none"

        termination_reason = "five_in_a_row" if state.winner != piece.EMPTY else "board_full"
        self._log(
            (
                f"Dual LLM game {round_number} finished. "
                f"Winner: {winner}. "
                f"Reason: {termination_reason}."
            ),
            board=state,
        )
        return self._build_game_report(
            round_number=round_number,
            first_color=first_color,
            moves=moves,
            state=state,
            outcome=outcome,
            winner=winner,
            termination_reason=termination_reason,
        )

    def _build_game_report(
        self,
        round_number,
        first_color,
        moves,
        state,
        outcome,
        winner,
        termination_reason,
        error=None,
    ):
        report = {
            "round": round_number,
            "first_player": color_name(first_color),
            "outcome": outcome,
            "winner": winner,
            "termination_reason": termination_reason,
            "moves": moves,
            "final_board": str(state),
        }
        if error:
            report["error"] = error
        return report

    def _player_for_color(self, color):
        return self.black_player if color == piece.BLACK else self.white_player

    def _config_for_color(self, color):
        return self.black_config if color == piece.BLACK else self.white_config

    def _first_color_for_round(self, round_number):
        if self.rounds == 1:
            return piece.BLACK
        return piece.WHITE if round_number % 2 == 1 else piece.BLACK

    def _log(self, message="", board=None):
        if not self.verbose:
            return

        self.stream.write("\n")
        if message:
            self.stream.write(f"{message}\n")
        if board is not None:
            self.stream.write(f"{board}\n")
        self.stream.flush()


def run_dual(args, start_callback=None):
    if args.rounds <= 0:
        raise ValueError("Rounds must be a positive integer.")

    black_config = load_model_config(model_file=args.black_llm_file)
    white_config = load_model_config(model_file=args.white_llm_file)
    runner = DualRunner(
        black_config=black_config,
        white_config=white_config,
        rounds=args.rounds,
        verbose=getattr(args, "verbose", False),
        debug_http=getattr(args, "debug_http", False),
    )
    if start_callback:
        start_callback(runner)
    report = runner.run()
    output_path = save_dual_report(runner.run_id, report)
    return output_path, report


def save_dual_report(run_id, report):
    DUAL_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DUAL_REPORT_DIR / f"dual-{run_id}.json"
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
    if outcome == "black_win":
        summary.black_wins += 1
    elif outcome == "white_win":
        summary.white_wins += 1
    else:
        summary.draws += 1
