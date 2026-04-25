import numpy as np

from libs import piece


def evaluation_state(state, current_color):
    return evaluate_color(state, piece.BLACK, current_color) + evaluate_color(
        state,
        piece.WHITE,
        current_color,
    )


def evaluate_color(state, color, current_color):
    values = state.values
    size = state.size
    is_current = color == current_color
    evaluation = 0

    for index in range(size):
        evaluation += evaluate_line(values[index, :], color, is_current)
        evaluation += evaluate_line(values[:, index], color, is_current)

    for index in range(-size + 5, size - 4):
        evaluation += evaluate_line(np.diag(values, k=index), color, is_current)
        evaluation += evaluate_line(np.diag(np.fliplr(values), k=index), color, is_current)

    return evaluation * color


def evaluate_line(line, color, current):
    evaluation = 0
    size = len(line)
    consecutive = 0
    block_count = 2
    has_empty_space = False

    for index in range(size):
        value = line[index]
        if value == color:
            consecutive += 1
        elif value == piece.EMPTY and consecutive > 0:
            if not has_empty_space and index < size - 1 and line[index + 1] == color:
                has_empty_space = True
            else:
                evaluation += calc(consecutive, block_count - 1, current, has_empty_space)
                consecutive = 0
                block_count = 1
                has_empty_space = False
        elif value == piece.EMPTY:
            block_count = 1
        elif consecutive > 0:
            evaluation += calc(consecutive, block_count, current)
            consecutive = 0
            block_count = 2
        else:
            block_count = 2

    if consecutive > 0:
        evaluation += calc(consecutive, block_count, current)

    return evaluation


def calc(consecutive, block_count, is_current, has_empty_space=False):
    if block_count == 2 and consecutive < 5:
        return 0

    if consecutive >= 5:
        if has_empty_space:
            return 8000
        return 100000

    consecutive_score = (2, 5, 1000, 10000)
    block_count_score = (0.5, 0.6, 0.01, 0.25)
    not_current_score = (1, 1, 0.2, 0.15)
    empty_space_score = (1, 1.2, 0.9, 0.4)

    score_index = consecutive - 1
    value = consecutive_score[score_index]
    if block_count == 1:
        value *= block_count_score[score_index]
    if not is_current:
        value *= not_current_score[score_index]
    if has_empty_space:
        value *= empty_space_score[score_index]
    return int(value)
