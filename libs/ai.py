import numpy as np

from libs import piece
from libs.eval_fn import evaluation_state


AI_LEVELS = {
    "easy": {
        "depth": 1,
        "candidate_count": 4,
        "random_top_n": 2,
    },
    "standard": {
        "depth": 2,
        "candidate_count": 10,
        "random_top_n": 1,
    },
    "hard": {
        "depth": 3,
        "candidate_count": 12,
        "random_top_n": 1,
    },
}


def resolve_ai_settings(level="standard", depth=None):
    if level not in AI_LEVELS:
        raise ValueError(f"Unknown AI level: {level}")

    settings = dict(AI_LEVELS[level])
    if depth is not None:
        settings["depth"] = depth
    settings["level"] = level
    return settings


def get_best_move(state, depth, is_max_state, candidate_count=10, random_top_n=1):
    values = state.values
    best_value = -9999 if is_max_state else 9999
    best_move = (-1, -1)
    pieces = len(values[values != piece.EMPTY])

    if pieces == 0:
        return first_move(state)
    if pieces == 1:
        return second_move(state)

    top_moves = get_top_moves(state, candidate_count, is_max_state)

    if random_top_n > 1 and len(top_moves) > 1:
        top_limit = min(random_top_n, len(top_moves))
        random_index = int(np.random.randint(0, top_limit))
        return top_moves[random_index]

    for move, _ in top_moves:
        move = tuple(int(value) for value in move)
        value = minimax(state.next(move), -10e5, 10e5, depth - 1, not is_max_state)

        if (is_max_state and value > best_value) or (not is_max_state and value < best_value):
            best_value = value
            best_move = move

    if best_move == (-1, -1):
        return top_moves[0]

    return best_move, best_value


def get_top_moves(state, count, is_max_state):
    color = state.color
    top_moves = []

    for move in state.legal_moves():
        move = tuple(int(value) for value in move)
        evaluation = evaluation_state(state.next(move), color)
        top_moves.append((move, evaluation))

    return sorted(top_moves, key=lambda item: item[1], reverse=is_max_state)[:count]


def minimax(state, alpha, beta, depth, is_max_state):
    if depth == 0 or state.is_terminal():
        return evaluation_state(state, -state.color)

    if is_max_state:
        value = -9999
        for move in state.legal_moves():
            value = max(value, minimax(state.next(move), alpha, beta, depth - 1, False))
            alpha = max(value, alpha)
            if alpha >= beta:
                break
        return value

    value = 9999
    for move in state.legal_moves():
        value = min(value, minimax(state.next(move), alpha, beta, depth - 1, True))
        beta = min(value, beta)
        if alpha >= beta:
            break
    return value


def first_move(state):
    center = state.size // 2
    return np.random.choice((center - 1, center, center + 1), 2), 1


def second_move(state):
    row, column = state.last_move
    size = state.size
    row_offset = 1 if row <= size // 2 else -1
    column_offset = 1 if column <= size // 2 else -1
    return (row + row_offset, column + column_offset), 2
