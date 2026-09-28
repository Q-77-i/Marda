"""ingest 的 SQLite 侧单测：拆表 schema、双写与全量同步、老库迁移（SPEC §8.1）。

Qdrant/嵌入侧走 test_ingest_qdrant 的 fake 面，这里只碰 SQLite。
"""

from __future__ import annotations

import sqlite3

import ingest

# 阶段 1 单源 schema（拆表前）：来源四要素平铺在 questions 上
OLD_DDL = """
CREATE TABLE questions (
  id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
  key_points JSON, follow_ups JSON, domain TEXT NOT NULL, topic TEXT NOT NULL,
  difficulty TEXT NOT NULL, company TEXT, round TEXT,
  source TEXT, license TEXT, url TEXT, status TEXT DEFAULT 'enabled'
)
"""


def question(
    *,
    question_id: str = "q_aaa",
    answer: str = "参考答案。",
    status: str = "enabled",
    sources: list[dict] | None = None,
) -> dict:
    return {
        "question_id": question_id,
        "question": "题干？",
        "answer": answer,
        "key_points": ["要点"],
        "follow_ups": ["追问？"],
        "topic": "检索",
        "domain": "rag",
        "difficulty": "L1",
        "company": "字节跳动",
        "round": "一面",
        "source": "个人题库-牛客补充版",
        "sources": sources
        if sources is not None
        else [
            {
                "source": "个人题库-牛客补充版",
                "license": "personal",
                "url": "https://www.nowcoder.com/discuss/1",
                "source_detail": "字节跳动一面面经",
            }
        ],
        "status": status,
    }


def rows(db_path, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def columns(db_path, table: str) -> set[str]:
    return {row[1] for row in rows(db_path, f"PRAGMA table_info({table})")}


def test_建表_questions只留主源明细进来源表(tmp_path):
    db = tmp_path / "marda.sqlite3"

    ingest.write_sqlite([question()], db)

    assert "source" in columns(db, "questions")
    assert not ({"license", "url", "source_detail"} & columns(db, "questions"))
    assert columns(db, "question_sources") == {
        "question_id", "source", "license", "url", "source_detail", "imported_at", "status",
    }
    assert rows(db, "SELECT question_id, source, license, url, source_detail, status FROM question_sources") == [
        (
            "q_aaa",
            "个人题库-牛客补充版",
            "personal",
            "https://www.nowcoder.com/discuss/1",
            "字节跳动一面面经",
            "enabled",
        )
    ]
    assert rows(db, "SELECT imported_at FROM question_sources")[0][0]  # 导入时间已写


def test_多源明细全进_题目只留主源(tmp_path):
    db = tmp_path / "marda.sqlite3"
    item = question(
        sources=[
            {"source": "ai-agent-interview-guide", "license": "MIT", "url": "", "source_detail": ""},
            {"source": "个人题库-牛客补充版", "license": "personal", "url": "", "source_detail": "字节一面"},
        ]
    )
    item["source"] = "个人题库-牛客补充版"

    ingest.write_sqlite([item], db)

    assert rows(db, "SELECT source FROM questions") == [("个人题库-牛客补充版",)]
    assert rows(db, "SELECT source FROM question_sources ORDER BY source") == [
        ("ai-agent-interview-guide",),
        ("个人题库-牛客补充版",),
    ]


def test_全量同步_消失的题与来源行一并删除(tmp_path):
    db = tmp_path / "marda.sqlite3"
    ingest.write_sqlite([question(question_id="q_aaa"), question(question_id="q_bbb")], db)

    ingest.write_sqlite([question(question_id="q_aaa")], db)

    assert rows(db, "SELECT id FROM questions") == [("q_aaa",)]
    assert rows(db, "SELECT question_id FROM question_sources") == [("q_aaa",)]


def test_重跑幂等_行数与主源不变(tmp_path):
    db = tmp_path / "marda.sqlite3"
    items = [question(question_id="q_aaa"), question(question_id="q_bbb")]

    ingest.write_sqlite(items, db)
    first = rows(db, "SELECT id, question, answer, source, status FROM questions ORDER BY id")
    first_sources = rows(
        db, "SELECT question_id, source, license, url, source_detail FROM question_sources ORDER BY question_id"
    )

    ingest.write_sqlite(items, db)

    assert rows(db, "SELECT id, question, answer, source, status FROM questions ORDER BY id") == first
    assert (
        rows(
            db,
            "SELECT question_id, source, license, url, source_detail FROM question_sources ORDER BY question_id",
        )
        == first_sources
    )
    assert rows(db, "SELECT COUNT(*) FROM question_sources") == [(2,)]


def _old_db(db_path) -> None:
    """造阶段 1 老库：来源四要素平铺，questions 里有一行真实数据。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(OLD_DDL)
        conn.execute(
            "INSERT INTO questions (id, question, answer, key_points, follow_ups, domain, topic,"
            " difficulty, company, round, source, license, url, status)"
            " VALUES ('q_old', '老题？', '老答案。', '[]', '[]', 'rag', '检索', 'L1',"
            " '字节跳动', '一面', '个人题库-牛客补充版', 'personal', 'https://x/1', 'enabled')"
        )


def test_迁移_老库回填来源并删列(tmp_path):
    db = tmp_path / "marda.sqlite3"
    _old_db(db)

    with sqlite3.connect(db) as conn:
        ingest.ensure_schema(conn)

    assert rows(db, "SELECT question_id, source, license, url, source_detail, status FROM question_sources") == [
        ("q_old", "个人题库-牛客补充版", "personal", "https://x/1", None, "enabled")
    ]
    assert not ({"license", "url"} & columns(db, "questions"))
    # 题目本体一行不动（迁移只搬来源字段）
    assert rows(db, "SELECT id, question, answer, source FROM questions") == [
        ("q_old", "老题？", "老答案。", "个人题库-牛客补充版")
    ]


def test_迁移幂等_重跑不重复(tmp_path):
    db = tmp_path / "marda.sqlite3"
    _old_db(db)

    with sqlite3.connect(db) as conn:
        ingest.ensure_schema(conn)
        ingest.ensure_schema(conn)

    assert rows(db, "SELECT COUNT(*) FROM question_sources") == [(1,)]
