import json
import re
import sys
import sysconfig
import threading
import time
from http.client import HTTPException
from pathlib import Path
from urllib import error, request

from libs import piece
from libs.eval_fn import evaluation_state


MOVE_PATTERN = re.compile(r"(\d+)\s*,\s*(\d+)")
RULE_PATH = Path(__file__).resolve().parent.parent / "rule.md"
PREFIX_RULE_PATH = Path(sys.prefix) / "rule.md"
DATA_RULE_PATH = Path(sysconfig.get_paths().get("data", sys.prefix)).resolve() / "rule.md"


def load_rule_context():
    candidate_paths = (
        Path.cwd() / "rule.md",
        PREFIX_RULE_PATH,
        PREFIX_RULE_PATH.resolve(),
        DATA_RULE_PATH,
        RULE_PATH,
    )
    seen = set()
    for rule_path in candidate_paths:
        path_key = str(rule_path)
        if path_key in seen:
            continue
        seen.add(path_key)
        try:
            rule_text = rule_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        return (
            "Reference rules from rule.md:\n"
            f"{rule_text}"
        )

    return (
        "Rules: Black is X, White is O, . means empty. Players alternate placing one stone "
        "on an empty position. Five or more in a row wins. Full board without five in a row is a draw."
    )


RULE_CONTEXT = load_rule_context()
COMPACT_RULE_CONTEXT = (
    "Rules: X and O alternate placing one stone on an empty point. "
    "You are given the exact legal moves for this turn. Choose exactly one of those legal moves. "
    "Five or more in a row horizontally, vertically, or diagonally wins. "
    "There are no captures, forbidden moves, swap rules, or pass moves."
)


class LLMMoveError(RuntimeError):
    pass


class LLMRequestError(LLMMoveError):
    pass


class RequestRateLimiter:
    def __init__(self, requests_per_minute):
        self.interval = 60.0 / requests_per_minute
        self.next_request_at = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_request_at - now)
            self.next_request_at = max(now, self.next_request_at) + self.interval

        if delay > 0:
            time.sleep(delay)


_RATE_LIMITERS = {}
_RATE_LIMITERS_LOCK = threading.Lock()


def rate_limiter_for(model_config):
    key = (
        model_config.provider_id,
        model_config.base_url,
        model_config.model_id,
        model_config.rate_limit_rpm,
    )
    with _RATE_LIMITERS_LOCK:
        limiter = _RATE_LIMITERS.get(key)
        if limiter is None:
            limiter = RequestRateLimiter(model_config.rate_limit_rpm)
            _RATE_LIMITERS[key] = limiter
        return limiter


class LLMPlayer:
    def __init__(self, model_config, timeout=None, debug_http=False, reasoning_log_path=None):
        self.model_config = model_config
        self.timeout = timeout if timeout is not None else model_config.timeout_seconds
        self.debug_http = debug_http
        self.reasoning_log_path = Path(reasoning_log_path) if reasoning_log_path else None
        self.rate_limiter = rate_limiter_for(model_config)

    def choose_move(self, game, llm_color):
        last_error = None
        attempt = 1
        while True:
            prompt = build_move_prompt(game, llm_color, attempt, last_error)
            response_text = self._chat(prompt, attempt=attempt)
            try:
                row, column = parse_move_response(response_text, game.size, game.state)
            except ValueError as error_message:
                last_error = f"Invalid response: {error_message}. Raw response: {response_text!r}"
                attempt += 1
                continue

            if not game.state.is_valid_position((row, column)):
                last_error = (
                    f"Illegal move {(column + 1)},{(row + 1)} because that position is not empty "
                    "or is outside the board."
                )
                attempt += 1
                continue

            return (row, column), response_text

    def _chat(self, prompt, attempt=None):
        return self._chat_openai_compatible(prompt, attempt=attempt)

    def _chat_openai_compatible(self, prompt, attempt=None):
        api_key = self.model_config.get_api_key()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": self.model_config.model_id,
            "messages": [
                {"role": "system", "content": move_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 1024,
        }
        payload.update(self.model_config.extra_body)
        stream_response = bool(payload.get("stream", False))
        headers["Accept"] = "text/event-stream" if stream_response else "application/json"
        endpoint = f"{self.model_config.base_url}/chat/completions"
        self.rate_limiter.wait()
        response_body = self._post_json(endpoint, headers, payload)

        message, reasoning = parse_chat_response(response_body, stream_response)
        content = message.get("content", "")

        self._log_raw_response(
            prompt=prompt,
            attempt=attempt,
            response_body=response_body,
            message=message,
            reasoning=reasoning,
        )

        return normalize_response_content(content, reasoning)

    def _post_json(self, endpoint, headers, payload):
        data = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = ""
            if self.debug_http:
                try:
                    error_body = exc.read().decode("utf-8")
                except Exception:
                    error_body = "<failed to read response body>"
                response_headers = dict(exc.headers.items()) if exc.headers else {}
                detail = (
                    f"\nHTTP status: {exc.code}"
                    f"\nResponse headers: {json.dumps(response_headers, indent=2)}"
                    f"\nResponse body: {error_body}"
                )
            raise LLMRequestError(f"Request to {endpoint} failed: {exc}{detail}") from exc
        except (error.URLError, HTTPException, OSError) as exc:
            raise LLMRequestError(f"Request to {endpoint} failed: {exc}") from exc

    def _log_raw_response(self, prompt, attempt, response_body, message, reasoning):
        if not self.reasoning_log_path:
            return

        self.reasoning_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_entry = {
            "model": self.model_config.model_id,
            "attempt": attempt,
            "prompt": prompt,
            "reasoning": reasoning,
            "message": message,
            "raw_response": response_body,
        }
        with self.reasoning_log_path.open("a", encoding="utf-8") as handle:
            json.dump(log_entry, handle, ensure_ascii=False)
            handle.write("\n")


def move_system_prompt():
    return (
        "You are an expert Gomoku player participating in a benchmark match "
        "against a deterministic alpha-beta engine. Read the rules and board "
        "state carefully, then choose one legal move. Your reply must start "
        "with a legal move in x,y format using 1-based coordinates, for example "
        "10,10. Valid examples: 10,10 or 3,14. Invalid examples: (10,10), x=10 y=10, "
        "row 10 column 10, or any sentence before the move. Do not put any text before the move. "
        "You must always make a legal move while the game is still active. Do not resign, pass, "
        "stop, concede, or only explain that the position is already won or lost; even if the result looks "
        "forced or defeat is unavoidable, choose a legal move that lets the game continue to its actual terminal state."
    )


def normalize_response_content(content, reasoning=""):
    if content is None:
        content = ""

    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )

    content = str(content).strip()
    if content:
        return content

    return str(reasoning).strip()


def parse_chat_response(response_body, stream_response=False):
    if stream_response:
        return parse_streaming_chat_response(response_body)

    try:
        response_payload = json.loads(response_body)
        message = response_payload["choices"][0]["message"]
        reasoning = message.get("reasoning") or response_payload["choices"][0].get("reasoning", "")
        return message, reasoning
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMRequestError(f"Unexpected model response: {response_body}") from exc


def parse_streaming_chat_response(response_body):
    content_parts = []
    reasoning_parts = []

    for raw_line in response_body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue

        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMRequestError(f"Unexpected streaming model response: {response_body}") from exc

        choices = chunk.get("choices") or []
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta") or choice.get("message") or {}
        if isinstance(delta, dict):
            append_content_piece(content_parts, delta.get("content"))
            append_content_piece(
                reasoning_parts,
                delta.get("reasoning") or delta.get("reasoning_content"),
            )

        append_content_piece(content_parts, choice.get("text"))
        append_content_piece(
            reasoning_parts,
            choice.get("reasoning") or choice.get("reasoning_content"),
        )

    message = {"content": "".join(content_parts)}
    reasoning = "".join(reasoning_parts)
    return message, reasoning


def append_content_piece(parts, piece_value):
    if piece_value is None:
        return

    if isinstance(piece_value, list):
        for item in piece_value:
            if isinstance(item, dict):
                append_content_piece(parts, item.get("text"))
            else:
                append_content_piece(parts, item)
        return

    parts.append(str(piece_value))


def build_move_prompt(game, llm_color, attempt, last_error):
    color_name = "black (X)" if llm_color == 1 else "white (O)"
    opponent_name = "white (O)" if llm_color == 1 else "black (X)"
    legal_moves = [move_to_text(move) for move in ranked_legal_moves(game.state, llm_color)]
    legal_move_text = ", ".join(legal_moves)

    prompt_lines = [
        "You are taking the next turn in an active Gomoku game.",
        f"Your color: {color_name}.",
        f"Opponent color: {opponent_name}.",
        f"Board size: {game.size}x{game.size}.",
        "Coordinate format: x,y where x is the column and y is the row, both 1-based.",
        "Goal: choose the strongest legal move for your side right now.",
        COMPACT_RULE_CONTEXT,
        "Critical instruction: trust the LEGAL_MOVES list below. Do not recalculate whether a listed move is empty.",
        "The game is not over yet. You must make a legal move to finish the game on the board.",
        "Never resign, pass, stop playing, concede, or answer only that the game is already won or lost.",
        "Even if you believe a win or loss is forced, still output one legal move and continue the game.",
        "Important response rule: the first characters of your reply must be exactly one legal move such as 10,10.",
        "Output format rule: write only digits, then a comma, then digits at the start of your reply.",
        "Valid examples: 10,10 and 3,14.",
        "Invalid examples: (10,10), x=10,y=10, row 10 column 10, Move: 10,10, or any explanation before the move.",
        "You may add short reasoning after the move if you want, but the move must come first.",
        "Finish every turn until the benchmark game engine reports the final result.",
    ]

    if legal_moves:
        prompt_lines.extend(
            [
                f"LEGAL_MOVES: {legal_move_text}",
                f"If unsure, choose this legal fallback move: {legal_moves[0]}",
            ]
        )
    else:
        prompt_lines.extend(
            [
                "LEGAL_MOVES: any empty coordinate on the board.",
                "If unsure, choose this legal fallback move: 10,10",
            ]
        )

    prompt_lines.extend(
        [
            "Current board symbols: X = black, O = white, . = empty.",
            "Current board:",
            str(game.state),
        ]
    )

    if not legal_moves:
        prompt_lines.extend(
            [
                "The board is empty, so the center 10,10 is legal and strong.",
            ]
        )

    if last_error:
        prompt_lines.extend(
            [
                "",
                f"Previous attempt {attempt - 1} was rejected.",
                f"Reason: {last_error}",
                f"Do not repeat the rejected move. Choose one exact coordinate from LEGAL_MOVES: {legal_move_text}",
                "Try again and start your reply with one legal x,y move.",
                "Reminder: the first characters must look exactly like 10,10 with no words or punctuation before it.",
            ]
        )

    return "\n".join(prompt_lines)


def parse_move_response(response_text, board_size, board_state=None):
    matches = list(MOVE_PATTERN.finditer(response_text))
    if not matches:
        raise ValueError("response did not include x,y coordinates")

    first_in_bounds = None
    for match in matches:
        x_value = int(match.group(1))
        y_value = int(match.group(2))
        if not (1 <= x_value <= board_size and 1 <= y_value <= board_size):
            continue

        move = (y_value - 1, x_value - 1)
        if first_in_bounds is None:
            first_in_bounds = move
        if board_state is None or board_state.is_valid_position(move):
            return move

    if first_in_bounds is not None:
        return first_in_bounds

    raise ValueError(f"coordinates must be between 1 and {board_size}")


def move_to_text(move):
    row, column = move
    return f"{column + 1},{row + 1}"


def ranked_legal_moves(state, color):
    moves = [tuple(int(value) for value in move) for move in state.legal_moves()]
    reverse = color == piece.BLACK
    scored_moves = [
        (move, evaluation_state(state.next(move), color))
        for move in moves
    ]
    return [
        move
        for move, _ in sorted(scored_moves, key=lambda item: item[1], reverse=reverse)
    ]
