"""题库浏览查询单测（FR-12）：筛选/分页/分面/供给统计/来源主源排序。

表结构与造数走 bank_fixture（列契约的单一来源——本文件曾自带一份 DDL 副本，
M7 加 user_id 时它立刻过期，故收回共享夹具）。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.tools import bank_query
from bank_fixture import create_tables, insert_questions, insert_sources, question_row


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "bank.sqlite3"
    create_tables(path)
    insert_questions(path, [
        question_row("q1"),
        question_row("q2", domain="rag", difficulty="L2"),
        question_row("q3", domain="memory", difficulty="L3", company=None, round_=None),
        question_row("q4", domain="rag", difficulty="L1", status="draft"),
    ])
    insert_sources(path, [
        ("q1", "个人题库", "PRIVATE", "https://x/1", "牛客补充版", "enabled"),
        ("q1", "FAQ_Of_LLM_Interview", "MIT", "https://x/2", "faq.md", "enabled"),
        ("q1", "归档源", "MIT", "https://x/3", "old.md", "archived"),
        ("q3", "llm-interview-guide", "MIT", "https://x/4", None, "enabled"),
    ])
    return path


def test_浏览只含enabled并解析json字段(db_path):
    items, total = bank_query.browse_questions(db_path)
    assert total == 3  # q4 是 draft
    assert {i["question_id"] for i in items} == {"q1", "q2", "q3"}
    first = next(i for i in items if i["question_id"] == "q1")
    assert first["key_points"] == ["k1", "k2"]
    assert first["follow_ups"] == ["追问"]


def test_筛选与分页(db_path):
    items, total = bank_query.browse_questions(db_path, filters={"domain": "rag"})
    assert (total, [i["question_id"] for i in items]) == (2, ["q1", "q2"])

    items, total = bank_query.browse_questions(
        db_path, filters={"domain": "rag", "difficulty": "L2"}
    )
    assert (total, [i["question_id"] for i in items]) == (1, ["q2"])

    page2, total = bank_query.browse_questions(db_path, page=2, page_size=2)
    assert total == 3
    assert len(page2) == 1  # 第三页只剩 1 条（排序 domain, difficulty, id → memory/L3 在末位）


def test_空值筛选不参与(db_path):
    """company=None / 空串 = 不筛（前端"全部"选项）。"""
    _, total = bank_query.browse_questions(db_path, filters={"company": None, "round": ""})
    assert total == 3


def test_page_size越界夹紧(db_path):
    items, _ = bank_query.browse_questions(db_path, page_size=999)
    assert len(items) == 3  # 夹到上限 50，不足则全出


def test_来源明细_主源排首位且跳过归档(db_path):
    items, _ = bank_query.browse_questions(db_path)
    bank_query.attach_sources(db_path, items)
    q1 = next(i for i in items if i["question_id"] == "q1")
    assert [s["source"] for s in q1["sources"]] == ["个人题库", "FAQ_Of_LLM_Interview"]  # 主源首位、archived 剔除
    assert q1["sources"][0]["license"] == "PRIVATE"
    q2 = next(i for i in items if i["question_id"] == "q2")
    assert q2["sources"] == []  # 无来源明细不报错


def test_难度分面按档位排序(db_path):
    """难度是有序维度：L2 计数最多也排在 L1 之后（计数序对档位是噪音，M6 验收反馈）。

    其余三维保持「计数降序」——厂商/面次没有天然顺序，问得多的排前面才有用。
    """
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO questions (id, question, answer, domain, topic, difficulty, status)"
            " VALUES (?, ?, '答案', 'rag', '测试主题', 'L2', 'enabled')",
            [("q_l2_a", "题干"), ("q_l2_b", "题干")],
        )
    facets = bank_query.bank_facets(db_path)
    assert {f["value"]: f["count"] for f in facets["difficulty"]} == {"L1": 1, "L2": 3, "L3": 1}
    assert [f["value"] for f in facets["difficulty"]] == ["L1", "L2", "L3"]


def test_分面计数(db_path):
    facets = bank_query.bank_facets(db_path)
    assert {f["value"]: f["count"] for f in facets["domain"]} == {"rag": 2, "memory": 1}
    assert [f["value"] for f in facets["difficulty"]] == ["L1", "L2", "L3"]  # 计数全 1 → 按值升序
    assert [f["value"] for f in facets["company"]] == ["腾讯"]  # NULL 不入分面
    assert [f["value"] for f in facets["round"]] == ["一面"]


def test_供给统计按难度域聚合(db_path):
    supply = bank_query.difficulty_supply(db_path)
    assert supply == {"L1": {"rag": 1}, "L2": {"rag": 1}, "L3": {"memory": 1}}  # draft 不计
