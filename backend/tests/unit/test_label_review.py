"""复核产物渲染单测（P1-M12 会话 1）。

复核文件是「人审 golden set」这条主张的载体，它自己得先是对的：
「最该看的行」挑错了，人工那 10–15 分钟就白花。
"""

from __future__ import annotations

from evals.label_relevance import TOP_N, render_review

ROWS = {
    "a": {"question": "工具调用失败怎么办？重试与幂等"},
    "b": {"question": "MCP 协议解决了什么问题"},
    "c": {"question": "什么是 Function Calling"},
}


def _entry(**over) -> dict:
    base = {
        "id": "q01",
        "scene": "bank_search",
        "query": "工具调用失败怎么处理",
        "domain": None,
        "grades": {"a": 2, "b": 1, "c": 0},
        "ranked": ["a", "c", "b"],
        "missing": [],
    }
    base.update(over)
    return base


def _render(entries, *, notes=()) -> str:
    return render_review(entries, ROWS, created="2026-10-01", extra_notes=notes)


def test_头部统计计数正确():
    text = _render([_entry()])
    assert "1 条 query" in text
    assert "标注候选 3 条（直接相关 1 / 部分相关 1 / 不相关 1）" in text


def test_topN_行带排名与判定():
    text = _render([_entry()])
    assert "| 1 | ✓2 | 工具调用失败怎么办？重试与幂等 |" in text
    assert "| 2 | ✗0 | 什么是 Function Calling |" in text
    assert "| 3 | ✓1 | MCP 协议解决了什么问题 |" in text


def test_不相关却排前面进最该看的行():
    text = _render([_entry()])
    assert "最该看的行" in text
    assert "`q01 #2`" in text
    assert "什么是 Function Calling" in text  # 行里必须带题干，只给 id 人工没法判


def test_直接相关排太后不进最该看的行():
    # 深埋的 ✓2 是**检索漏检**（NDCG 要测的信号），不是标注分歧——两版试错的结论都钉在这
    ranked = [f"g{i}" for i in range(5)] + [f"x{i}" for i in range(5)] + ["a"]
    grades = {**{f"g{i}": 1 for i in range(5)}, "a": 2}
    text = _render([_entry(ranked=ranked, grades=grades)])
    assert "## 最该看的行" not in text


def test_判定与排名同向不占复核版面():
    text = _render([_entry(ranked=["b", "a", "c"], grades={"a": 2, "b": 1, "c": 0})])
    assert "`q01 #1`" not in text
    assert "`q01 #2`" not in text


def test_未进前N的相关题单独列出():
    ranked = [f"x{i}" for i in range(TOP_N)] + ["a"]
    text = _render([_entry(ranked=ranked, grades={"a": 2})])
    assert f"未进前 {TOP_N} 的相关题" in text
    assert f"`#{TOP_N + 1}`" in text


def test_未判出的_id_明示不静默():
    text = _render([_entry(missing=["c"])])
    assert "⚠️ 未判出（按 0 计）：c" in text


def test_构建告警段仅在有时出现():
    assert "构建告警" not in _render([_entry()])
    assert "构建告警" in _render([_entry()], notes=["q09 候选池为空"])


def test_长题干截断不破表():
    rows = {"a": {"question": "很长的题干" * 30}}
    text = render_review([_entry(grades={"a": 2}, ranked=["a"])], rows, created="2026-10-01")
    assert "…" in text
    assert all(line.count("|") <= 4 for line in text.splitlines() if line.startswith("| "))
