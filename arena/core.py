"""Game logic shared by the Static Space and the tournament runner."""

import json

from libs import piece
from libs.ai import get_best_move, resolve_ai_settings
from libs.board import BoardState
from libs.llm_player import move_to_text, parse_move_response


SIZE = 19
COLORS = {piece.BLACK: "black", piece.WHITE: "white"}
SYSTEM = "Play freestyle Gomoku. Reply with one legal x,y move and nothing else."


class ArenaGame:
    def __init__(self, opening=()):
        self.state = BoardState(SIZE)
        self.moves = []
        for move in opening:
            self.apply(move)

    def apply(self, text):
        if self.state.is_terminal():
            raise ValueError("The game has ended.")
        move = parse_move_response(text, SIZE, self.state)
        if not self.state.is_valid_position(move):
            raise ValueError("That intersection is occupied.")
        self.state = self.state.next(move)
        self.moves.append({"color": COLORS[self.state.color], "move": move_to_text(move)})

    def oracle_move(self):
        color = -self.state.color
        settings = resolve_ai_settings("standard")
        move, _ = get_best_move(
            self.state, settings["depth"], color == piece.BLACK,
            candidate_count=settings["candidate_count"],
            random_top_n=settings["random_top_n"],
        )
        return move_to_text(move)

    def request(self, template=None, retry=False):
        color = -self.state.color
        if template is None:
            return {
                "system": SYSTEM,
                "prompt": self.prompt(color, retry=retry) + "\n\n" + str(self.state),
            }

        moves = self.candidate_moves()
        return {
            "prompt": format_prompt(self.choice_prompt(moves), template),
            "grammar": "root ::= " + " | ".join(json.dumps(move) for move in moves),
        }

    def candidate_moves(self):
        candidates = self.state.legal_moves()
        if not self.moves:
            center = SIZE // 2
            candidates = [(y, x) for y in range(center - 1, center + 2)
                          for x in range(center - 1, center + 2)]
        return [move_to_text(move) for move in candidates]

    def choice_prompt(self, moves):
        return self.prompt(-self.state.color) + "\nChoose one of: " + " ".join(moves) + "\nMove:"

    def prompt(self, color, retry=False):
        occupied = {
            side: " ".join(m["move"] for m in self.moves if m["color"] == side) or "none"
            for side in COLORS.values()
        }
        instruction = "Your previous reply was invalid. " if retry else ""
        return (
            "The board is 19x19. Coordinates x,y are column,row from 1 through 19. "
            "Five or more in a horizontal, vertical or diagonal line wins. "
            "Take an immediate win if possible, otherwise block one.\n"
            f"Black stones: {occupied['black']}\nWhite stones: {occupied['white']}\n"
            f"You are {COLORS[color]}. {instruction}Reply with one empty coordinate."
        )

    def snapshot(self):
        finished = self.state.is_terminal()
        winner = COLORS.get(self.state.winner)
        return {
            "moves": self.moves,
            "winner": winner,
            "next": None if finished else COLORS[-self.state.color],
            "reason": "five" if winner else ("draw" if finished else None),
        }


def format_prompt(prompt, template):
    if template == "zephyr":
        return f"<|system|>\n{SYSTEM}</s>\n<|user|>\n{prompt}</s>\n<|assistant|>\n"
    return f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"


game = None


def browser_command(serialized):
    global game
    command = json.loads(serialized)
    action = command["action"]
    if action == "start":
        game = ArenaGame()
    if action == "request":
        return json.dumps(game.request(command.get("template"), command.get("retry", False)))
    if action == "oracle":
        return json.dumps(game.oracle_move())
    if action == "move":
        try:
            game.apply(command["move"])
        except ValueError:
            return json.dumps({"invalid": True})
    return json.dumps(game.snapshot())
