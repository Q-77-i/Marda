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
    """按当前题评分更新难度与连击计数（原地，无返回值）。

    固定难度场次（P1-M6 FR-14：创建时选了 L1/L2/L3）直接返回——难度由用户指定，
    连击不累计（累计了也无处可用：唯一消费者是升降档）。
    """
    if state.difficulty_locked:
        return
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
