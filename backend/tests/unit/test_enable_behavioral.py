"""启用行为面题库脚本的单测（P1-M11 D6）：目标状态的判定逻辑。

只测纯函数 `plan_changes`（Qdrant/嵌入侧由脚本自身的核对步骤在真链路验收，
dry-run 由人工跑，见会话记录）。
"""

from __future__ import annotations

import enable_behavioral


def _row(question_id: str, *, answer: str, status: str = "draft") -> dict:
    return {
        "id": question_id, "question": "讲讲你的项目", "answer": answer,
        "key_points": "[]", "follow_ups": "[]", "domain": "behavioral",
        "topic": "项目表达", "difficulty": "L1", "company": None, "round": None,
        "source": "个人题库", "status": status,
    }


def test_有实质答案的翻_enabled_空答案的留_draft():
    rows = [
        _row("q_ok", answer="先讲背景，再讲行动，最后给量化结果。"),
        _row("q_empty", answer=""),
        _row("q_short", answer="xx"),  # 实质字符数 < 5，占位级
    ]

    changes = enable_behavioral.plan_changes(rows)

    assert [(c["id"], c["target_status"]) for c in changes] == [("q_ok", "enabled")]


def test_已_enabled_的题不产生改动():
    """幂等：重复跑脚本时 only-changes 为空，不产生空写入。"""
    rows = [_row("q_ok", answer="先讲背景，再讲行动，最后给量化结果。", status="enabled")]

    assert enable_behavioral.plan_changes(rows) == []


def test_目标是_draft_的也纳入改动():
    """反向也成立：答案被清空的题要从 enabled 退回 draft（脚本会同步撤点）。"""
    rows = [_row("q_bad", answer="", status="enabled")]

    changes = enable_behavioral.plan_changes(rows)

    assert [(c["id"], c["target_status"]) for c in changes] == [("q_bad", "draft")]
