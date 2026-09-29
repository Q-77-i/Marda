"""题库测试夹具（单测/集成共用）：表结构与 data/scripts/ingest 的 DDL 一致。

questions / question_sources 由语料管道建表，不在 app.db.ensure_schema 里，
故测试自建；列名与生产一致是**契约**，改 DDL 时这里要同步。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE questions (
  id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
  key_points JSON, follow_ups JSON, domain TEXT NOT NULL, topic TEXT NOT NULL,
  difficulty TEXT NOT NULL, company TEXT, round TEXT, source TEXT,
  status TEXT DEFAULT 'enabled'
);
CREATE TABLE question_sources (
  question_id TEXT NOT NULL, source TEXT NOT NULL, license TEXT, url TEXT,
  source_detail TEXT, imported_at TEXT, status TEXT DEFAULT 'enabled',
  PRIMARY KEY (question_id, source)
);
"""


def question_row(
    qid: str,
    *,
    domain: str = "rag",
    difficulty: str = "L1",
    company: str | None = "腾讯",
    round_: str | None = "一面",
    status: str = "enabled",
    source: str = "个人题库",
) -> dict:
    return {
        "id": qid, "question": f"{qid} 题干", "answer": f"{qid} 答案",
        "key_points": json.dumps(["k1", "k2"], ensure_ascii=False),
        "follow_ups": json.dumps(["追问"], ensure_ascii=False),
        "domain": domain, "topic": "测试主题", "difficulty": difficulty,
        "company": company, "round": round_, "source": source, "status": status,
    }


def create_tables(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(DDL)


def insert_questions(path: Path, rows: list[dict]) -> None:
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO questions (id, question, answer, key_points, follow_ups, domain,"
            " topic, difficulty, company, round, source, status)"
            " VALUES (:id, :question, :answer, :key_points, :follow_ups, :domain, :topic,"
            " :difficulty, :company, :round, :source, :status)",
            rows,
        )


def insert_sources(path: Path, rows: list[tuple]) -> None:
    """rows = [(question_id, source, license, url, source_detail, status)]。"""
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO question_sources (question_id, source, license, url, source_detail,"
            " status) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
