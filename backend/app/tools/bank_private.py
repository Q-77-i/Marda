"""私有题库查询层（FR-13，SPEC §8 questions.user_id / §7 私有题库三端点）：上传入库 / 列表 / 编辑 / 归档。

**user_id 必传无默认**（同 db.list_interviews 口径）：隔离靠每个查询都带归属条件，
漏传在调用处直接 TypeError，而不是静默查出别人的题。

私有题与公共题**同表**（questions.user_id：NULL = 公共、非空 = 归属用户），但**不进 Qdrant**：
出题时公共候选走 Qdrant payload 过滤、私有候选走本模块的 SQL，两者在 question_search 合并。
私有题的 SQL 只在本模块出现一处，其他模块（浏览/检索/容量）一律经这里取。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 来源名进 question_sources（M5 拆表后的合规四要素）：私有题无外链、license 记 personal
PRIVATE_SOURCE = "个人上传"
PRIVATE_LICENSE = "personal"
DEFAULT_TOPIC = "个人上传"

PAGE_SIZE = 10
MAX_PAGE_SIZE = 50

# 可编辑列：题干改动不重算 id（id 是导入时定的不透明主键，改了题干仍是同一条记录）
EDITABLE = ("question", "answer", "key_points", "follow_ups", "topic", "domain", "difficulty", "status")
STATUS_OPTIONS = ("enabled", "draft")  # enabled = 使用中；draft = 已归档（复用既有状态枚举）

_FIELDS = (
    "id, question, answer, key_points, follow_ups, domain, topic, difficulty, "
    "company, round, source, status"
)


def private_id(user_id: str, text: str) -> str:
    """私有题 id = `p_` + md5(user_id + 题干)[:12]。

    前缀 `p_` 与公共题的 `q_` 天然不撞；把 user_id 拌进摘要，两个用户上传同一道题
    也各自成条、互不覆盖（公共题按题干合并是跨源同题的语义，私有题不能套用）。
    """
    digest = hashlib.md5(f"{user_id}\n{text}".encode("utf-8")).hexdigest()[:12]
    return f"p_{digest}"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["question_id"] = item.pop("id")
    item["key_points"] = json.loads(item["key_points"]) if item["key_points"] else []
    item["follow_ups"] = json.loads(item["follow_ups"]) if item["follow_ups"] else []
    return item


def insert_questions(
    db_path: Path, *, user_id: str, records: list[dict], source_detail: str = ""
) -> dict[str, Any]:
    """私有题入库，返回 `{"imported": n, "duplicated": [题干…]}`。

    **重复题 DO NOTHING，绝不覆盖**：同 id（同用户 + 同题干）已存在时保留原记录——
    用户可能已经编辑过它，重传一份文件不该把他的修改冲掉。已归档的题同样算重复
    （靠列表的状态筛选找回，不靠重传）。

    records 字段：text / answer / key_points / follow_ups / topic + **domain / difficulty**
    （后两者来自上传表单的批量默认值，文件里不写——不该让用户背内部枚举值）。
    """
    rows = [
        {
            "id": private_id(user_id, record["text"]),
            "question": record["text"],
            "answer": record["answer"],
            "key_points": json.dumps(record.get("key_points") or [], ensure_ascii=False),
            "follow_ups": json.dumps(record.get("follow_ups") or [], ensure_ascii=False),
            "topic": record.get("topic") or DEFAULT_TOPIC,
            "domain": record["domain"],
            "difficulty": record["difficulty"],
            "source": PRIVATE_SOURCE,
            "status": "enabled",
            "user_id": user_id,
        }
        for record in records
    ]
    if not rows:
        return {"imported": 0, "duplicated": []}
    with _connect(db_path) as conn:
        placeholders = ",".join("?" * len(rows))
        existing = {
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM questions WHERE user_id=? AND id IN ({placeholders})",
                [user_id, *(row["id"] for row in rows)],
            )
        }
        fresh = [row for row in rows if row["id"] not in existing]
        conn.executemany(
            "INSERT INTO questions (id, question, answer, key_points, follow_ups, domain,"
            " topic, difficulty, company, round, source, status, user_id)"
            " VALUES (:id, :question, :answer, :key_points, :follow_ups, :domain, :topic,"
            " :difficulty, NULL, NULL, :source, :status, :user_id)",
            fresh,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO question_sources"
            " (question_id, source, license, url, source_detail, imported_at, status)"
            " VALUES (?, ?, ?, '', ?, ?, ?)",
            [
                (row["id"], PRIVATE_SOURCE, PRIVATE_LICENSE, source_detail, _now(), row["status"])
                for row in fresh
            ],
        )
    return {
        "imported": len(fresh),
        "duplicated": [row["question"] for row in rows if row["id"] in existing],
    }


def _now() -> str:
    """UTC ISO 时间戳（与 app/db.py 同口径）。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def list_questions(
    db_path: Path,
    *,
    user_id: str,
    status: str | None = None,
    domain: str | None = None,
    difficulty: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """该用户的私有题分页（含已归档），返回 (items, total)。

    按 **rowid 倒序** = 最近上传的在前：私有题 id 是内容寻址的哈希，按 id 排等于随机序，
    用户刚传完一 file 却要翻到末页才看得到。rowid 是插入序（questions 非 WITHOUT ROWID），
    分页因此稳定、翻页不重不漏。
    """
    clauses, params = ["user_id=?"], [user_id]
    for column, value in (("status", status), ("domain", domain), ("difficulty", difficulty)):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    where = " AND ".join(clauses)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (max(1, page) - 1) * page_size
    with _connect(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM questions WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_FIELDS} FROM questions WHERE {where}"
            f" ORDER BY rowid DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
    return [_item(row) for row in rows], total


def get_question(db_path: Path, *, user_id: str, question_id: str) -> dict[str, Any] | None:
    """按 id 取**本人的**私有题；不存在或非本人一律 None（调用方按 404 处理，不泄露存在性）。"""
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {_FIELDS} FROM questions WHERE id=? AND user_id=?", (question_id, user_id)
        ).fetchone()
    return _item(row) if row else None


def update_question(
    db_path: Path, *, user_id: str, question_id: str, fields: dict[str, Any]
) -> bool:
    """编辑/归档本人私有题；返回是否有行被更新（非本人 → False → 404）。

    只接受 EDITABLE 中的列，其余键忽略（API 层已用模型约束，这里是第二道闸）。
    """
    sets, params = [], []
    for key in EDITABLE:
        if key not in fields:
            continue
        value = fields[key]
        if key in ("key_points", "follow_ups"):
            value = json.dumps(value or [], ensure_ascii=False)
        sets.append(f"{key}=?")
        params.append(value)
    if not sets:
        return False
    with _connect(db_path) as conn:
        updated = conn.execute(
            f"UPDATE questions SET {', '.join(sets)} WHERE id=? AND user_id=?",
            [*params, question_id, user_id],
        ).rowcount
        # 来源明细的 status 跟随题目（M5 口径：该来源的答案是否可用）
        if "status" in fields:
            conn.execute(
                "UPDATE question_sources SET status=? WHERE question_id=?", (fields["status"], question_id)
            )
    return updated > 0


def search_candidates(
    db_path: Path,
    *,
    user_id: str,
    domain: str,
    difficulty: str,
    exclude_ids: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """出题检索的私有候选（仅 enabled），出参形状与 question_search.fetch_by_ids 一致。

    与公共候选（Qdrant payload 过滤）在 question_search 合并后一起随机——
    私有题不进 Qdrant，故这里的 SQL 就是它在检索侧的**唯一**入口。
    """
    clauses = ["user_id=?", "status='enabled'", "domain=?", "difficulty=?"]
    params: list[Any] = [user_id, domain, difficulty]
    exclude = list(exclude_ids or [])
    if exclude:
        clauses.append(f"id NOT IN ({','.join('?' * len(exclude))})")
        params.extend(exclude)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_FIELDS} FROM questions WHERE {' AND '.join(clauses)} LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [_item(row) for row in rows]
