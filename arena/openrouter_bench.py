"""Benchmark the current OpenRouter free chat models against GomokuBench."""

import argparse
import getpass
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from arena.publish import DEFAULT_OPENROUTER
from libs import piece
from libs.game import GameSession
from libs.llm_player import (
    build_move_prompt,
    move_system_prompt,
    move_to_text,
    normalize_response_content,
    parse_move_response,
)


API = "https://openrouter.ai/api/v1/"
SIDES = {"black": piece.BLACK, "white": piece.WHITE}
CAPS = {"prompt": 0, "completion": 0, "request": 0}
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class SweepStopped(RuntimeError):
    pass


class MoveUnavailable(RuntimeError):
    def __init__(self, message, retryable=False, retry_delay=0):
        super().__init__(message)
        self.retryable = retryable
        self.retry_delay = retry_delay


class NoLegalMove(MoveUnavailable):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def eligible_models(data):
    result = {}
    for model in data:
        model_id = model["id"]
        architecture = model["architecture"]
        pricing = model.get("pricing", {})
        if (
            model_id.endswith(":free")
            and "text" in architecture.get("input_modalities", [])
            and architecture.get("output_modalities") == ["text"]
            and "prompt" in pricing
            and "completion" in pricing
            and all(Decimal(str(pricing.get(field, 0))) == 0 for field in CAPS)
        ):
            result[model_id] = {"id": model_id, "name": model["name"],
                                "canonical": model.get("canonical_slug") or model_id}
    return result


class Router:
    def __init__(self, client, catalog):
        self.client = client
        self.catalog = catalog
        quota = self.get_quota()
        self.limit = int(quota["remaining"])
        self.attempts = 0
        self.verified = 0
        self.reason = None
        self.next_request = 0.0
        self.lock = threading.Lock()

    def get_quota(self):
        response = self.client.get("key")
        if response.is_error:
            raise SystemExit(f"OpenRouter key check returned HTTP {response.status_code}.")
        quota = response.json()["data"].get("free_model_daily_requests")
        if quota is None:
            raise SystemExit("OpenRouter did not report a free-model allowance.")
        return quota

    def stop(self, reason):
        with self.lock:
            self.reason = reason
        raise SweepStopped(reason)

    def take_request(self):
        while True:
            with self.lock:
                if self.reason:
                    raise SweepStopped(self.reason)
                if self.attempts >= self.limit:
                    self.reason = "Daily free request allowance reached"
                    raise SweepStopped(self.reason)
                delay = self.next_request - time.monotonic()
                if delay <= 0:
                    self.attempts += 1
                    self.next_request = time.monotonic() + 3.1
                    return
            time.sleep(min(delay, 3.1))

    def chat(self, model_id, prompt, max_tokens, system):
        if model_id not in self.catalog:
            raise MoveUnavailable("Free endpoint left the catalog")
        self.take_request()
        started = time.monotonic()
        try:
            request_body = {
                "model": model_id,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": False,
                "provider": {"max_price": CAPS, "sort": "price"},
                "plugins": [{"id": "web", "enabled": False}],
                "usage": {"include": True},
            }
            with self.client.stream("POST", "chat/completions", json=request_body) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > 100:
                        raise MoveUnavailable("Provider response took more than 100 seconds", retryable=True)
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise MoveUnavailable("Provider response exceeded the size limit")
        except httpx.TransportError:
            raise MoveUnavailable("Provider connection timed out or failed", retryable=True) from None
        try:
            payload = json.loads(body)
        except ValueError:
            raise MoveUnavailable(f"OpenRouter returned HTTP {response.status_code} without chat JSON",
                                  retryable=response.status_code in RETRYABLE) from None

        failure = payload.get("error") or {}
        status = response.status_code if response.is_error else failure.get("code")
        if status:
            metadata = failure.get("metadata") or {}
            source = str(metadata.get("limit_source", ""))
            if status in (401, 402):
                self.stop(f"OpenRouter account returned HTTP {status}")
            if status == 429 and ("daily" in source or "per_day" in source):
                self.stop("Daily free request allowance reached")
            if status == 429 and "per_minute" in source:
                with self.lock:
                    self.next_request = max(self.next_request, time.monotonic() + 10)
            retry_delay = 0
            if status == 429:
                retry_after = response.headers.get("retry-after", "")
                retry_delay = int(retry_after) if retry_after.isdecimal() else 18
                if retry_delay > 60:
                    raise MoveUnavailable("Free provider rate limited with a longer retry window")
            raise MoveUnavailable(f"OpenRouter/provider HTTP {status}", retryable=status in RETRYABLE,
                                  retry_delay=retry_delay)

        usage = payload.get("usage") or {}
        if usage.get("is_byok") or (usage.get("cost") is not None and Decimal(str(usage["cost"])) != 0):
            self.stop("Unexpected billing metadata from OpenRouter")
        if usage.get("cost") is None:
            raise MoveUnavailable("OpenRouter did not confirm the response was free")
        with self.lock:
            self.verified += 1

        model = self.catalog[model_id]
        actual = str(payload.get("model", "")).removesuffix(":free")
        if actual not in {model_id.removesuffix(":free"), model["canonical"].removesuffix(":free")}:
            raise MoveUnavailable("Response model ID did not match the requested model")
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        reasoning = message.get("reasoning") or message.get("reasoning_content") or choice.get("reasoning")
        return normalize_response_content(message.get("content"), reasoning), choice.get("finish_reason")


class Checkpoint:
    def __init__(self, path, models):
        self.path = path
        self.lock = threading.Lock()
        if path.is_file():
            self.data = json.loads(path.read_text())
            self.data.pop("updatedAt", None)
        else:
            self.data = {
                "startedAt": utc_now(),
                "models": [
                    {**model, "games": [
                        {"color": color, "moves": [], "status": "queued"} for color in SIDES
                    ]}
                    for model in models.values()
                ],
                "requests": 0,
                "verifiedFreeResponses": 0,
            }
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=self.path.parent, encoding="utf-8", delete=False) as file:
            json.dump(self.data, file, separators=(",", ":"))
            file.write("\n")
        Path(file.name).replace(self.path)

    def update(self, game, **changes):
        with self.lock:
            game.update(changes)
            self.save()


def choose_move(router, model_id, session):
    return choose_router_move(
        router, model_id, session.state, move_system_prompt(),
        lambda attempt, error: build_move_prompt(session, session.human_color, attempt, error),
    )


def choose_router_move(router, model_id, state, system, prompt_for_attempt, allowed=None):
    last_error = None
    invalid = 0
    unavailable = 0
    max_tokens = 2048
    while invalid < 5:
        attempt = invalid + 1
        prompt = prompt_for_attempt(attempt, last_error)
        try:
            text, finish = router.chat(model_id, prompt, max_tokens, system)
        except MoveUnavailable as error:
            unavailable += 1
            if not error.retryable or unavailable == 3:
                raise
            time.sleep(error.retry_delay)
            continue
        try:
            move = parse_move_response(text, state.size, state)
            coordinate = move_to_text(move)
            if state.is_valid_position(move) and (allowed is None or coordinate in allowed):
                return move
            last_error = f"The point {coordinate} is unavailable. Use a coordinate from the offered list."
        except (ValueError, TypeError):
            last_error = "The reply did not supply an empty coordinate. Use a coordinate from the offered list."
        invalid += 1
        if finish == "length":
            max_tokens = min(6144, max_tokens + 2048)
    raise NoLegalMove("Five replies without a legal move")


def play_model(model, game, router, checkpoint):
    model_id, player = model["id"], game["color"]
    if game["status"] == "complete":
        return
    session = GameSession(human_color=SIDES[player], ai_level="standard")
    moves = list(game["moves"])
    for record in moves:
        move = parse_move_response(record["move"], session.size, session.state)
        if not session.state.is_valid_position(move):
            raise ValueError("The saved checkpoint has an illegal move.")
        session.state = session.state.next(move)

    print(f"Playing {model_id} as {player} from move {len(moves)}", flush=True)
    checkpoint.update(game, status="playing", reason=None)
    try:
        while not session.finished:
            color = session.next_player_color()
            if session.human_to_move():
                move = choose_move(router, model_id, session)
                session.play_human(*move)
            else:
                move = session.play_ai()
            moves.append({"color": "black" if color == piece.BLACK else "white", "move": move_to_text(move)})
            checkpoint.update(game, moves=moves.copy())
    except (SweepStopped, MoveUnavailable) as error:
        status = "pending" if isinstance(error, SweepStopped) else "unavailable"
        checkpoint.update(game, status=status, reason=str(error))
        print(f"{model_id} as {player}: {error} after {len(moves)} moves", flush=True)
        return

    winner = {piece.BLACK: "black", piece.WHITE: "white"}.get(session.winner)
    checkpoint.update(game, status="complete", reason="five" if winner else "draw", winner=winner)
    result = "draw" if winner is None else ("model won" if winner == player else "Oracle won")
    print(f"{model_id} as {player}: {result} in {len(moves)} moves", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_OPENROUTER)
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 7))
    args = parser.parse_args()
    key = getpass.getpass("OpenRouter credential (hidden input): ") if args.prompt_key else os.getenv("OPENROUTER_API_KEY", "")
    if not re.fullmatch(r"sk-or-v1-[a-f0-9]{64}", key):
        raise SystemExit("Provide an OpenRouter key in OPENROUTER_API_KEY or use --prompt-key.")

    with httpx.Client(base_url=API, headers={"Authorization": "Bearer " + key},
                      follow_redirects=False, timeout=httpx.Timeout(90, connect=12)) as client:
        models = client.get("models", params={"input_modalities": "text", "output_modalities": "text"})
        models.raise_for_status()
        catalog = eligible_models(models.json()["data"])
        if not catalog:
            raise SystemExit("OpenRouter did not list any free text models.")
        checkpoint = Checkpoint(args.checkpoint, catalog)
        router = Router(client, catalog)
        print(f"{len(catalog)} free chat models. {router.limit} free requests available.", flush=True)
        workers = args.workers or min(6, max(1, router.limit // 15))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for color in SIDES:
                jobs = [
                    pool.submit(play_model, model, game, router, checkpoint)
                    for model in checkpoint.data["models"]
                    for game in model["games"]
                    if game["color"] == color and game["status"] != "complete" and not router.reason
                ]
                for job in jobs:
                    job.result()
        checkpoint.data["updatedAt"] = utc_now()
        checkpoint.data["requests"] += router.attempts
        checkpoint.data["verifiedFreeResponses"] += router.verified
        checkpoint.data["stopReason"] = router.reason
        checkpoint.save()
        print(f"Run used {router.attempts} attempts. {router.verified} responses confirmed $0.", flush=True)
        print("Free-model request counter:", router.get_quota(), flush=True)


if __name__ == "__main__":
    main()
