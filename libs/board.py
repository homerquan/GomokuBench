import numpy as np

from libs import piece


class BoardState:
    def __init__(self, size, values=None, color=piece.WHITE):
        if np.all(values != None):
            self.values = np.copy(values)
        else:
            self.values = np.full((size, size), piece.EMPTY)

        self.size = size
        self.color = color
        self.last_move = None
        self.winner = piece.EMPTY

    def value(self, position):
        return self.values[position]

    def is_valid_position(self, position):
        return is_valid_position(self.size, position) and self.values[position] == piece.EMPTY

    def legal_moves(self):
        prev_move_idxs = self.values != piece.EMPTY
        area_idxs = expand_area(self.size, prev_move_idxs)
        return np.column_stack(np.where(area_idxs == True))

    def next(self, position):
        next_state = BoardState(size=self.size, values=self.values, color=-self.color)
        next_state[position] = next_state.color
        next_state.last_move = tuple(position)
        return next_state

    def is_terminal(self):
        is_win, _ = self.check_five_in_a_row()
        return is_win or self.is_full()

    def check_five_in_a_row(self):
        pattern = np.full((5,), 1)

        black_win = self.check_pattern(pattern * piece.BLACK)
        white_win = self.check_pattern(pattern * piece.WHITE)

        if black_win:
            self.winner = piece.BLACK
            return True, piece.BLACK
        if white_win:
            self.winner = piece.WHITE
            return True, piece.WHITE
        return False, piece.EMPTY

    def is_full(self):
        return not np.any(self.values == piece.EMPTY)

    def check_pattern(self, pattern):
        count = 0
        for line in self.get_lines():
            if issub(line, pattern):
                count += 1
        return count

    def get_lines(self):
        lines = []

        for index in range(self.size):
            lines.append(self.values[index, :])
            lines.append(self.values[:, index])

        for index in range(-self.size + 5, self.size - 4):
            lines.append(np.diag(self.values, k=index))
            lines.append(np.diag(np.fliplr(self.values), k=index))

        for line in lines:
            yield line

    def __getitem__(self, position):
        row, column = position
        return self.values[row, column]

    def __setitem__(self, position, value):
        row, column = position
        self.values[row, column] = value

    def __str__(self):
        cell_width = 3
        header = " " * 4 + "".join(f"{column + 1:<{cell_width}}" for column in range(self.size))
        rows = [header]

        for row in range(self.size):
            cells = []
            for column in range(self.size):
                symbol = piece.SYMBOLS[self[row, column]]
                cells.append(f"{symbol:<{cell_width}}")
            rows.append(f"{row + 1:>2}  " + "".join(cells))

        return "\n".join(rows)

    def __repr__(self):
        return str(self)


def issub(line, subline):
    line_size = len(line)
    subline_size = len(subline)
    for index in range(line_size - subline_size + 1):
        current = line[index:index + subline_size]
        if (current == subline).all():
            return True
    return False


def expand_area(size, indexes):
    area_indexes = np.copy(indexes)
    for row in range(size):
        for column in range(size):
            if not indexes[row, column]:
                continue
            for delta_row, delta_column in ((1, 0), (0, 1), (1, 1), (1, -1)):
                for side in (1, -1):
                    next_row = row + delta_row * side
                    next_column = column + delta_column * side
                    if not is_valid_position(size, (next_row, next_column)):
                        continue
                    area_indexes[next_row, next_column] = True
    return np.bitwise_xor(area_indexes, indexes)


def is_valid_position(board_size, position):
    row, column = position
    return 0 <= row < board_size and 0 <= column < board_size
