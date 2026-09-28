"""入库：富化后的题目 JSON → SQLite（业务库）+ Qdrant（向量库）双写，幂等。

- SQLite 表结构见 SPEC §8；这里只建/写 questions + question_sources 两表（interviews 等由 T4/T5 负责）
- **来源拆表（M5，SPEC §8.1）**：questions.source = 答案主源；合规四要素明细进
  question_sources（license 按源记、不按题记），一题多源 = 多行
- Qdrant collection `questions`：每题一 doc，**命名双向量**（M3）
  dense（本地 BGE-M3 1024d）+ sparse（BGE-M3 lexical weights，服务端 RRF 用）
  布局与 embedding 服务见 backend/embedding_service/
- 只入 status=enabled 的题；draft 题只进 SQLite，不进向量库

用法：docker compose up -d embedding && python data/scripts/ingest.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from app.config import get_settings
from app.tools.embedding import EmbeddingClient, question_doc_text, to_sparse_vector

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = REPO_ROOT / "data" / "parsed" / "questions_enriched.json"
COLLECTION = "questions"
DENSE = "dense"
SPARSE = "sparse"
VECTOR_SIZE = 1024  # BGE-M3

DDL = """
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  key_points JSON,
  follow_ups JSON,
  domain TEXT NOT NULL,
  topic TEXT NOT NULL,
  difficulty TEXT NOT NULL,
  company TEXT,
  round TEXT,
  source TEXT,
  status TEXT DEFAULT 'enabled'
)
"""

# 来源明细（SPEC §8.1）：一题多源 = 多行；status 跟随题目状态（该来源的答案是否可用）
SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS question_sources (
  question_id TEXT NOT NULL,
  source TEXT NOT NULL,
  license TEXT,
  url TEXT,
  source_detail TEXT,
  imported_at TEXT,
  status TEXT DEFAULT 'enabled',
  PRIMARY KEY (question_id, source)
)
"""


def _qdrant_payload(question: dict) -> dict:
    """payload 只放过滤/展示需要的字段（SPEC §5：domain/difficulty 过滤 + 排除 asked_ids）。"""
    return {
        "question_id": question["question_id"],
        "domain": question["domain"],
        "topic": question["topic"],
        "difficulty": question["difficulty"],
        "company": question["company"],
        "round": question["round"],
    }


def point_id(question_id: str) -> str:
    """确定性 point id：md5(question_id) → UUID。

    ⚠️ 不能用 enumerate 索引：题目合并/增量重跑后顺序会变，同题落到不同 id、
    旧点残留错位。内容寻址才能让 upsert 真幂等。
    """
    return str(uuid.UUID(bytes=hashlib.md5(question_id.encode("utf-8")).digest()))


def _now() -> str:
    """UTC ISO 时间戳（与 app/db.py 同口径）。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建两表 + 老库迁移（幂等，可重复调用）。"""
    conn.execute(DDL)
    conn.execute(SOURCES_DDL)
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """阶段 1 单源 schema → 拆表（SPEC §8.1）：来源四要素迁入 question_sources 后删列。

    只做一次，靠列探测判断（拆完后 license/url 不存在，再调用即返回）。
    source_detail 老库从未落库，回填 NULL——重跑管道时由 JSON 补上。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(questions)")}
    if not ({"license", "url"} & columns):
        return
    conn.execute(
        "INSERT OR IGNORE INTO question_sources"
        " (question_id, source, license, url, source_detail, imported_at, status)"
        " SELECT id, source, license, url, NULL, ?, status FROM questions",
        (_now(),),
    )
    for column in ("license", "url"):
        if column in columns:
            conn.execute(f"ALTER TABLE questions DROP COLUMN {column}")


def write_sqlite(questions: list[dict], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO questions (id, question, answer, key_points, follow_ups, domain, topic,
                                   difficulty, company, round, source, status)
            VALUES (:id, :question, :answer, :key_points, :follow_ups, :domain, :topic,
                    :difficulty, :company, :round, :source, :status)
            ON CONFLICT(id) DO UPDATE SET
              question=excluded.question, answer=excluded.answer, key_points=excluded.key_points,
              follow_ups=excluded.follow_ups, domain=excluded.domain, topic=excluded.topic,
              difficulty=excluded.difficulty, company=excluded.company, round=excluded.round,
              source=excluded.source, status=excluded.status
            """,
            [
                {
                    "id": q["question_id"],
                    "question": q["question"],
                    "answer": q["answer"],
                    "key_points": json.dumps(q.get("key_points") or [], ensure_ascii=False),
                    "follow_ups": json.dumps(q.get("follow_ups") or [], ensure_ascii=False),
                    "domain": q["domain"],
                    "topic": q["topic"],
                    "difficulty": q["difficulty"],
                    "company": q["company"],
                    "round": q["round"],
                    "source": q["source"],
                    "status": q["status"],
                }
                for q in questions
            ],
        )
        # 全量同步语义：管道是 questions 表的单一来源，题目被合并/删除后
        # upsert-only 会残留旧行（Qdrant 侧靠删 collection 重建，这里靠显式删除）
        ids = {q["question_id"] for q in questions}
        conn.execute(f"DELETE FROM questions WHERE id NOT IN ({','.join('?' for _ in ids)})", tuple(ids))
        # 来源明细整表重建：同步规则比 questions 多一维（题目还在、某来源没了也要删），
        # 逐行 diff 徒增复杂度；表只有千行量级，重建是毫秒级且绝无残留
        conn.execute("DELETE FROM question_sources")
        imported_at = _now()
        conn.executemany(
            "INSERT INTO question_sources"
            " (question_id, source, license, url, source_detail, imported_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    q["question_id"],
                    record["source"],
                    record.get("license"),
                    record.get("url"),
                    record.get("source_detail"),
                    imported_at,
                    q["status"],
                )
                for q in questions
                for record in q["sources"]
            ],
        )
        conn.commit()


async def write_qdrant(questions: list[dict], settings, *, client=None, embedder=None) -> int:
    """重建 collection 并全量写入 dense + sparse 双向量（幂等：每次都是重建）。

    重建而非增量：命名双向量布局是 RRF prefetch 的硬前提，Qdrant 不支持把
    无名字段在线改成命名；出题检索走 payload 过滤、不碰向量，重建期间无停机影响。
    client/embedder 可注入（单测用 fake，不打真实 Qdrant/嵌入服务）。
    """
    own_client = client is None
    client = client or AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
    embedder = embedder or EmbeddingClient(settings.embedding_url)

    if COLLECTION in {c.name for c in (await client.get_collections()).collections}:
        await client.delete_collection(COLLECTION)
    await client.create_collection(
        collection_name=COLLECTION,
        vectors_config={DENSE: VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)},
        sparse_vectors_config={SPARSE: SparseVectorParams()},
    )

    vectors = await embedder.embed(
        [question_doc_text(q["question"], q.get("key_points") or []) for q in questions]
    )
    if len(vectors) != len(questions):
        raise RuntimeError(f"向量数 {len(vectors)} 与题目数 {len(questions)} 不一致")

    points = []
    for question, vector in zip(questions, vectors):
        if len(vector.dense) != VECTOR_SIZE:
            raise RuntimeError(f"{question['question_id']} dense 维度 {len(vector.dense)} != {VECTOR_SIZE}")
        points.append(
            PointStruct(
                id=point_id(question["question_id"]),
                vector={DENSE: vector.dense, SPARSE: to_sparse_vector(vector.sparse)},
                payload=_qdrant_payload(question),
            )
        )
    await client.upsert(collection_name=COLLECTION, points=points, wait=True)
    if own_client:
        await client.close()
    print(f"已重建 collection：{COLLECTION}（命名双向量 dense {VECTOR_SIZE}d cosine + sparse）")
    return len(points)


def main() -> None:
    parser = argparse.ArgumentParser(description="题目 JSON → SQLite + Qdrant")
    parser.add_argument("--in", dest="in_path", type=Path, default=DEFAULT_IN)
    parser.add_argument("--dry-run", action="store_true", help="只写 SQLite，不调嵌入、不写 Qdrant")
    args = parser.parse_args()

    settings = get_settings()
    questions = json.loads(args.in_path.read_text(encoding="utf-8"))["questions"]
    enabled = [q for q in questions if q["status"] == "enabled"]
    missing = [q["question_id"] for q in enabled if not q.get("key_points") or not q.get("follow_ups")]
    if missing:
        raise SystemExit(f"有 {len(missing)} 题缺 key_points/follow_ups（先跑 enrich.py）：{missing[:5]}")
    no_sources = [q["question_id"] for q in questions if not q.get("sources")]
    if no_sources:
        raise SystemExit(
            f"有 {len(no_sources)} 题缺 sources 来源明细（JSON 是拆表前的旧格式，重跑 parse_md.py）："
            f"{no_sources[:5]}"
        )

    print(f"读入 {len(questions)} 题，其中 enabled {len(enabled)} 题待入库")

    write_sqlite(questions, settings.db_path)
    with sqlite3.connect(settings.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        enabled_count = conn.execute("SELECT COUNT(*) FROM questions WHERE status='enabled'").fetchone()[0]
        source_rows = conn.execute("SELECT COUNT(DISTINCT source) FROM question_sources").fetchone()[0]
        source_records = conn.execute("SELECT COUNT(*) FROM question_sources").fetchone()[0]
    print(
        f"SQLite：{settings.db_path} → questions 表 {total} 行（enabled {enabled_count}）；"
        f"question_sources 表 {source_records} 行（{source_rows} 个来源）"
    )

    if args.dry_run:
        print("--dry-run：跳过 Qdrant")
        return

    try:
        written = asyncio.run(write_qdrant(enabled, settings))
    except httpx.TransportError as exc:  # 最常见的失手：忘了起嵌入服务
        raise SystemExit(f"嵌入服务不可达（{settings.embedding_url}）：先 docker compose up -d embedding") from exc
    print(f"Qdrant：{settings.qdrant_url} collection `{COLLECTION}` → upsert {written} 点（dense + sparse）")


if __name__ == "__main__":
    main()
