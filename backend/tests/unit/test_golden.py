"""golden 查询集校验单测（P1-M12 会话 1）。

校验是 golden 的守门人：漏掉「一条 query 没有任何相关项」这类问题，指标会静默
算出 0 分并拉低均值——比报错难查得多，所以每条规则都钉一个用例。
"""

from __future__ import annotations

import json

import pytest

from evals import golden


def _doc(**overrides) -> dict:
    base = {
        "version": 1,
        "created": "2026-10-01",
        "pool": {"policy": "union(hybrid@30, dense@20, sparse@20)"},
        "grading": golden.GRADE_MEANINGS,
        "queries": [
            {
                "id": "q01",
                "scene": "bank_search",
                "query": "工具调用失败怎么处理",
                "filters": None,
                "source": "M3 探针",
                "grades": {"q_a": 2, "q_b": 1, "q_c": 0},
            }
        ],
    }
    base.update(overrides)
    return base


def test_合法文件零问题():
    assert golden.validate(_doc()) == []


def test_缺顶层字段逐条列全():
    problems = golden.validate({"version": 1})
    assert len(problems) == 4  # created / pool / grading / queries
    assert all(p.startswith("缺少顶层字段") for p in problems)


def test_id_重复被拦():
    item = _doc()["queries"][0]
    problems = golden.validate(_doc(queries=[item, dict(item)]))
    assert any("id 重复" in p for p in problems)


def test_scene_白名单():
    item = {**_doc()["queries"][0], "scene": "domain_label"}
    problems = golden.validate(_doc(queries=[item]))
    assert any("scene 必须是" in p for p in problems)


def test_filters_未知维度被拦():
    item = {**_doc()["queries"][0], "filters": {"domain": "rag", "topic": "x"}}
    problems = golden.validate(_doc(queries=[item]))
    assert any("未知维度" in p for p in problems)


def test_grade_越界被拦():
    item = {**_doc()["queries"][0], "grades": {"q_a": 3}}
    problems = golden.validate(_doc(queries=[item]))
    assert any("取值越界" in p for p in problems)


def test_无相关项的_query_被拦():
    # 全是 grade 0 的 query 指标恒 0，会静默拉低均值——必须在入口炸
    item = {**_doc()["queries"][0], "grades": {"q_a": 0, "q_b": 0}}
    problems = golden.validate(_doc(queries=[item]))
    assert any("没有任何 grade ≥ 1" in p for p in problems)


def test_空_query_文本被拦():
    item = {**_doc()["queries"][0], "query": "   "}
    problems = golden.validate(_doc(queries=[item]))
    assert any("query 文本为空" in p for p in problems)


def test_load_把全部问题拼进异常(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="golden 文件不合法"):
        golden.load(path)


def test_dump_load_往返一致(tmp_path):
    doc = _doc()
    path = tmp_path / "g.json"
    golden.dump(doc, path)
    assert golden.load(path) == doc
    assert "工具调用失败" in path.read_text(encoding="utf-8")  # 中文不转义


def test_relevant_ids_取_grade_大于等于_1():
    assert golden.relevant_ids(_doc()["queries"][0]) == {"q_a", "q_b"}
