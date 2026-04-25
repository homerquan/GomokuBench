from libs import piece
from libs.ai import get_best_move, resolve_ai_settings
from libs.board import BoardState


class GameSession:
    def __init__(self, size=19, depth=None, human_color=piece.BLACK, ai_level="standard"):
        self.size = size
        self.ai_settings = resolve_ai_settings(level=ai_level, depth=depth)
        self.depth = self.ai_settings["depth"]
        self.ai_level = self.ai_settings["level"]
        self.human_color = human_color
        self.ai_color = -human_color
        self.is_max_state = self.ai_color == piece.BLACK
        self.state = BoardState(size=self.size)

    @property
    def finished(self):
        return self.state.is_terminal()

    @property
    def winner(self):
        return self.state.winner

    def next_player_color(self):
        return -self.state.color

    def human_to_move(self):
        return self.next_player_color() == self.human_color

    def ai_to_move(self):
        return self.next_player_color() == self.ai_color

    def play_human(self, row, column):
        position = (row, column)
        if not self.human_to_move() or not self.state.is_valid_position(position):
            return False
        self.state = self.state.next(position)
        return True

    def play_ai(self):
        if not self.ai_to_move():
            return None

        move, _ = get_best_move(
            self.state,
            self.depth,
            self.is_max_state,
            candidate_count=self.ai_settings["candidate_count"],
            random_top_n=self.ai_settings["random_top_n"],
        )
        move = tuple(int(value) for value in move)
        self.state = self.state.next(move)
        return move
