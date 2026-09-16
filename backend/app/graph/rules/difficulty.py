"""难度自适应（纯代码，SPEC §4.3）。

连击判定：五维均值 ≥4 记好、≤2 记差、居中清零；连好 2 次升一档（封顶 L3），
连差 2 次降一档（保底 L1），升降后清零。原地修改 state（签名随 SPEC §4.3）。
"""

from __future__ import annotations

from app.graph.state import InterviewState

GOOD_MEAN = 4.0
BAD_MEAN = 2.0
STREAK_LIMIT = 2

DIFFICULTY_ORDER = ("L1", "L2", "L3")


def _shift(difficulty: str, delta: int) -> str:
    """升降一档，封顶 L3、保底 L1。"""
    index = DIFFICULTY_ORDER.index(difficulty) + delta
    return DIFFICULTY_ORDER[max(0, min(len(DIFFICULTY_ORDER) - 1, index))]


def update_difficulty(state: InterviewState) -> None:
    """按当前题评分更新难度与连击计数（原地，无返回值）。"""
    score = state.current_question.score if state.current_question else None
    if score is None:
        return
    mean = score.mean
    if mean >= GOOD_MEAN:
        state.consecutive_good += 1
        state.consecutive_bad = 0
    elif mean <= BAD_MEAN:
        state.consecutive_bad += 1
        state.consecutive_good = 0
    else:
        state.consecutive_good = 0
        state.consecutive_bad = 0
    if state.consecutive_good >= STREAK_LIMIT:
        state.difficulty = _shift(state.difficulty, +1)
        state.consecutive_good = 0
        state.consecutive_bad = 0
    elif state.consecutive_bad >= STREAK_LIMIT:
        state.difficulty = _shift(state.difficulty, -1)
        state.consecutive_good = 0
        state.consecutive_bad = 0
