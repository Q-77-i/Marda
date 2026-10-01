"""评分 golden 的格式校验（P1-M12 会话 2）：坏样本必须在 load 时就炸。

这些校验挡的是「指标悄悄失真」——期望分缺一维、组内题干不一致、臂与类型标错，
每一条都会让报告读到错误的数，而不是报错。
"""

from __future__ import annotations

import copy

from evals.judge_golden import slice_items, validate

TECH_DIMS = {
    "technical_depth": 3,
    "fundamentals": 3,
    "project_experience": 3,
    "communication": 3,
    "problem_solving": 3,
}


def _item(**over):
    base = {
        "id": "real-001",
        "arm": "real",
        "interview_type": "tech",
        "question": "题目",
        "key_points": ["a", "b"],
        "answer": "回答",
        "expected": {"dims": copy.deepcopy(TECH_DIMS), "covered_indexes": [0], "note": "理由"},
    }
    base.update(over)
    return base


def _doc(items=None, **over):
    base = {"version": 1, "created": "2026-10-01", "runs": 5, "temperature": 0.3,
            "items": items if items is not None else [_item()]}
    base.update(over)
    return base


def test_合法文档零问题():
    assert validate(_doc()) == []


def test_缺顶层字段一次列全():
    problems = validate({"version": 1})
    assert any("created" in p for p in problems)
    assert any("items" in p for p in problems)
    assert len(problems) >= 4


def test_id_重复被拦():
    problems = validate(_doc([_item(), _item()]))
    assert any("id 重复" in p for p in problems)


def test_臂与类型绑定被校验():
    problems = validate(_doc([_item(arm="behavioral", interview_type="tech")]))
    assert any("arm=behavioral" in p for p in problems)


def test_期望维度键必须与类型维度表一致():
    dims = copy.deepcopy(TECH_DIMS)
    del dims["problem_solving"]
    problems = validate(_doc([_item(expected={"dims": dims, "covered_indexes": []})]))
    assert any("维度键必须" in p for p in problems)


def test_期望分越界被拦():
    dims = copy.deepcopy(TECH_DIMS)
    dims["communication"] = 6
    problems = validate(_doc([_item(expected={"dims": dims, "covered_indexes": []})]))
    assert any("越界" in p for p in problems)


def test_覆盖下标越界被拦():
    problems = validate(_doc([_item(expected={
        "dims": copy.deepcopy(TECH_DIMS), "covered_indexes": [0, 5], "note": ""})]))
    assert any("covered_indexes 越界" in p for p in problems)


def _persona(tier, iid, question="同一道题"):
    return _item(
        id=iid, arm="persona", interview_type="tech", question=question,
        key_points=["a", "b"], group="g1", tier=tier,
    )


def test_persona_组内题干必须一致():
    """不同题的弱中强不构成单调性证据——这是会话 2 拍板口径②的守门测试。"""
    items = [_persona("weak", "p1", "题一"), _persona("medium", "p2", "题二"), _persona("strong", "p3", "题一")]
    problems = validate(_doc(items))
    assert any("题干不一致" in p for p in problems)


def test_persona_同档位重复被拦():
    items = [_persona("weak", "p1"), _persona("weak", "p2")]
    problems = validate(_doc(items))
    assert any("tier=weak 重复" in p for p in problems)


def test_组内少于两档被拦():
    problems = validate(_doc([_persona("weak", "p1")]))
    assert any("构成不了单调性检查" in p for p in problems)


def test_缺顶层_runs_校验其取值范围():
    assert any("runs" in p for p in validate(_doc(runs=1)))
    assert validate(_doc(runs=2)) == []


def test_切片按臂与类型():
    items = [
        _item(id="r1"),
        _item(id="b1", arm="behavioral", interview_type="behavioral",
              expected={"dims": {
                  "communication": 3, "logic_structure": 3, "project_experience": 3,
                  "values_motivation": 3, "career_stability": 3}, "covered_indexes": [], "note": ""}),
        _persona("weak", "p1"),
        _persona("medium", "p2"),
    ]
    doc = _doc(items)
    assert [i["id"] for i in slice_items(doc, arm="persona")] == ["p1", "p2"]
    assert [i["id"] for i in slice_items(doc, interview_type="behavioral")] == ["b1"]
    assert len(slice_items(doc)) == 4
