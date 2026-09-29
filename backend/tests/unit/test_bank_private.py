"""私有题库查询层（FR-13）：入库幂等/不覆盖、隔离、编辑归档、检索候选。"""

from __future__ import annotations

import sqlite3

import bank_fixture
import pytest

from app.tools import bank_private

USER = "u_aaa"
OTHER = "u_bbb"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "marda.sqlite3"
    bank_fixture.create_tables(path)
    return path


def record(text: str = "私有题？", *, domain: str = "rag", difficulty: str = "L1", **kw) -> dict:
    return {
        "text": text,
        "answer": "私有题参考答案内容。",
        "key_points": ["要点一"],
        "follow_ups": ["追问一"],
        "topic": "个人上传",
        "domain": domain,
        "difficulty": difficulty,
        **kw,
    }


def rows(db, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def test_上传入库_题目与来源明细齐落库(db):
    result = bank_private.insert_questions(
        db, user_id=USER, records=[record("私有题？")], source_detail="我的笔记.md"
    )

    assert result == {"imported": 1, "duplicated": []}
    qid = bank_private.private_id(USER, "私有题？")
    assert rows(db, "SELECT id, source, status, user_id FROM questions") == [
        (qid, "个人上传", "enabled", USER)
    ]
    assert rows(db, "SELECT question_id, source, license, source_detail FROM question_sources") == [
        (qid, "个人上传", "personal", "我的笔记.md")
    ]


def test_重复上传不覆盖已编辑内容(db):
    """重传同一份文件时必须保留用户的修改（DO NOTHING 而不是 upsert）。"""
    bank_private.insert_questions(db, user_id=USER, records=[record("私有题？")], source_detail="a.md")
    qid = bank_private.private_id(USER, "私有题？")
    bank_private.update_question(db, user_id=USER, question_id=qid, fields={"answer": "我改过的答案。"})

    result = bank_private.insert_questions(
        db, user_id=USER, records=[record("私有题？")], source_detail="a.md"
    )

    assert result == {"imported": 0, "duplicated": ["私有题？"]}
    assert rows(db, "SELECT answer FROM questions") == [("我改过的答案。",)]


def test_已归档的题重传也算重复不复活(db):
    """归档是用户的决定，重传同一份文件不该把它悄悄放回「使用中」。"""
    bank_private.insert_questions(db, user_id=USER, records=[record("私有题？")], source_detail="a.md")
    qid = bank_private.private_id(USER, "私有题？")
    bank_private.update_question(db, user_id=USER, question_id=qid, fields={"status": "draft"})

    result = bank_private.insert_questions(
        db, user_id=USER, records=[record("私有题？")], source_detail="a.md"
    )

    assert result == {"imported": 0, "duplicated": ["私有题？"]}
    assert rows(db, "SELECT status FROM questions") == [("draft",)]


def test_两个用户上传同一道题各自成条(db):
    """私有题不按题干跨用户合并（公共语的合并语义不能套用到这里）。"""
    bank_private.insert_questions(db, user_id=USER, records=[record("同一道题？")], source_detail="")
    bank_private.insert_questions(db, user_id=OTHER, records=[record("同一道题？")], source_detail="")

    assert bank_private.private_id(USER, "同一道题？") != bank_private.private_id(OTHER, "同一道题？")
    assert rows(db, "SELECT COUNT(*) FROM questions") == [(2,)]


def test_列表只看得到自己的题(db):
    bank_private.insert_questions(db, user_id=USER, records=[record("我的题？")], source_detail="")
    bank_private.insert_questions(db, user_id=OTHER, records=[record("别人的题？")], source_detail="")
    bank_fixture.insert_questions(db, [bank_fixture.question_row("q_pub")])

    items, total = bank_private.list_questions(db, user_id=USER)

    assert total == 1
    assert [item["question"] for item in items] == ["我的题？"]
    assert items[0]["question_id"] == bank_private.private_id(USER, "我的题？")


def test_列表含已归档且可按状态筛选(db):
    bank_private.insert_questions(
        db, user_id=USER, records=[record("在用题？"), record("归档题？")], source_detail=""
    )
    bank_private.update_question(
        db, user_id=USER, question_id=bank_private.private_id(USER, "归档题？"), fields={"status": "draft"}
    )

    assert bank_private.list_questions(db, user_id=USER)[1] == 2
    archived, total = bank_private.list_questions(db, user_id=USER, status="draft")
    assert total == 1 and archived[0]["question"] == "归档题？"


def test_列表分页不重不漏(db):
    bank_private.insert_questions(
        db, user_id=USER, records=[record(f"题{i}？") for i in range(5)], source_detail=""
    )

    page1, total = bank_private.list_questions(db, user_id=USER, page=1, page_size=2)
    page2, _ = bank_private.list_questions(db, user_id=USER, page=2, page_size=2)
    page3, _ = bank_private.list_questions(db, user_id=USER, page=3, page_size=2)

    assert total == 5
    seen = [item["question_id"] for item in [*page1, *page2, *page3]]
    assert len(seen) == 5 and len(set(seen)) == 5


def test_取单题按归属隔离(db):
    bank_private.insert_questions(db, user_id=OTHER, records=[record("别人的题？")], source_detail="")
    qid = bank_private.private_id(OTHER, "别人的题？")

    assert bank_private.get_question(db, user_id=OTHER, question_id=qid) is not None
    assert bank_private.get_question(db, user_id=USER, question_id=qid) is None


def test_编辑只改本人的题并同步来源状态(db):
    bank_private.insert_questions(db, user_id=USER, records=[record("私有题？")], source_detail="")
    qid = bank_private.private_id(USER, "私有题？")

    assert bank_private.update_question(
        db, user_id=USER, question_id=qid,
        fields={"question": "改过的题干？", "key_points": ["新要点"], "status": "draft"},
    )
    # 非本人编辑同样的行 → 不改动
    assert not bank_private.update_question(
        db, user_id=OTHER, question_id=qid, fields={"answer": "别人的答案。"}
    )

    assert rows(db, "SELECT question, key_points, status FROM questions") == [
        ("改过的题干？", '["新要点"]', "draft")
    ]
    assert rows(db, "SELECT status FROM question_sources") == [("draft",)]


def test_编辑忽略不可编辑的列(db):
    """user_id/id 这类列即使传进来也不该被改（第二道闸）。"""
    bank_private.insert_questions(db, user_id=USER, records=[record("私有题？")], source_detail="")
    qid = bank_private.private_id(USER, "私有题？")

    bank_private.update_question(
        db, user_id=USER, question_id=qid, fields={"user_id": OTHER, "id": "p_hack"}
    )

    assert rows(db, "SELECT id, user_id FROM questions") == [(qid, USER)]


def test_检索候选按域难度过滤且排除已问(db):
    bank_private.insert_questions(
        db, user_id=USER,
        records=[
            record("RAG的L1题？", domain="rag", difficulty="L1"),
            record("RAG的L2题？", domain="rag", difficulty="L2"),
            record("记忆的L1题？", domain="memory", difficulty="L1"),
        ],
        source_detail="",
    )

    hits = bank_private.search_candidates(db, user_id=USER, domain="rag", difficulty="L1")
    assert [item["question"] for item in hits] == ["RAG的L1题？"]

    excluded = bank_private.search_candidates(
        db, user_id=USER, domain="rag", difficulty="L1",
        exclude_ids=[bank_private.private_id(USER, "RAG的L1题？")],
    )
    assert excluded == []


def test_检索候选只取enabled且只取本人的(db):
    bank_private.insert_questions(
        db, user_id=USER, records=[record("在用题？"), record("归档题？")], source_detail=""
    )
    bank_private.update_question(
        db, user_id=USER, question_id=bank_private.private_id(USER, "归档题？"), fields={"status": "draft"}
    )
    bank_private.insert_questions(db, user_id=OTHER, records=[record("别人的题？")], source_detail="")

    hits = bank_private.search_candidates(db, user_id=USER, domain="rag", difficulty="L1")
    assert [item["question"] for item in hits] == ["在用题？"]
