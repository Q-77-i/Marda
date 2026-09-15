"""入库：富化后的题目 JSON → SQLite（业务库）+ Qdrant（向量库）双写，幂等。

- SQLite 表结构见 SPEC §8；这里只建/写 questions 表（interviews 等由 T4/T5 负责）
- Qdrant collection `questions`：每题一 doc，dense-only（SiliconFlow BGE-M3 1024d）
  ⚠️ demo 阶段不做 sparse/RRF：SiliconFlow 的 embedding API 只返回 dense，
     混合检索留到落地阶段换本地 BGE-M3 时启用（接口不变）
- 只入 status=enabled 的题；draft 题只进 SQLite，不进向量库

用法：python data/scripts/ingest.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = REPO_ROOT / "data" / "parsed" / "questions_enriched.json"
COLLECTION = "questions"
EMBED_BATCH = 16
EMBED_CONCURRENCY = 4
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
  license TEXT,
  url TEXT,
  status TEXT DEFAULT 'enabled'
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


async def embed_all(texts: list[str], settings) -> list[list[float]]:
    client = AsyncOpenAI(api_key=settings.siliconflow_api_key, base_url=settings.siliconflow_base_url, timeout=120.0)
    semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def batch(chunk: list[str]) -> list[list[float]]:
        async with semaphore:
            response = await client.embeddings.create(model=settings.embedding_model, input=chunk)
            return [item.embedding for item in response.data]

    chunks = [texts[i:i + EMBED_BATCH] for i in range(0, len(texts), EMBED_BATCH)]
    results = await asyncio.gather(*(batch(chunk) for chunk in chunks))
    await client.close()
    return [vector for group in results for vector in group]


def write_sqlite(questions: list[dict], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(DDL)
        conn.executemany(
            """
            INSERT INTO questions (id, question, answer, key_points, follow_ups, domain, topic,
                                   difficulty, company, round, source, license, url, status)
            VALUES (:id, :question, :answer, :key_points, :follow_ups, :domain, :topic,
                    :difficulty, :company, :round, :source, :license, :url, :status)
            ON CONFLICT(id) DO UPDATE SET
              question=excluded.question, answer=excluded.answer, key_points=excluded.key_points,
              follow_ups=excluded.follow_ups, domain=excluded.domain, topic=excluded.topic,
              difficulty=excluded.difficulty, company=excluded.company, round=excluded.round,
              source=excluded.source, license=excluded.license, url=excluded.url, status=excluded.status
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
                    "license": q["license"],
                    "url": q["url"],
                    "status": q["status"],
                }
                for q in questions
            ],
        )
        # 全量同步语义：管道是 questions 表的单一来源，题目被合并/删除后
        # upsert-only 会残留旧行（Qdrant 侧靠删 collection 重建，这里靠显式删除）
        ids = {q["question_id"] for q in questions}
        conn.execute(f"DELETE FROM questions WHERE id NOT IN ({','.join('?' for _ in ids)})", tuple(ids))
        conn.commit()


async def write_qdrant(questions: list[dict], settings) -> int:
    client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)

    existing = await client.get_collections()
    if COLLECTION not in {c.name for c in existing.collections}:
        await client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"已创建 collection：{COLLECTION}（{VECTOR_SIZE}d, cosine）")

    texts = [f"{q['question']}\n{q['topic']}" for q in questions]
    vectors = await embed_all(texts, settings)
    if len(vectors) != len(questions):
        raise RuntimeError(f"向量数 {len(vectors)} 与题目数 {len(questions)} 不一致")

    points = [
        PointStruct(id=point_id(question["question_id"]), vector=vector, payload=_qdrant_payload(question))
        for question, vector in zip(questions, vectors)
    ]
    await client.upsert(collection_name=COLLECTION, points=points, wait=True)
    await client.close()
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

    print(f"读入 {len(questions)} 题，其中 enabled {len(enabled)} 题待入库")

    write_sqlite(questions, settings.db_path)
    with sqlite3.connect(settings.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        enabled_count = conn.execute("SELECT COUNT(*) FROM questions WHERE status='enabled'").fetchone()[0]
    print(f"SQLite：{settings.db_path} → questions 表 {total} 行（enabled {enabled_count}）")

    if args.dry_run:
        print("--dry-run：跳过 Qdrant")
        return

    written = asyncio.run(write_qdrant(enabled, settings))
    print(f"Qdrant：{settings.qdrant_url} collection `{COLLECTION}` → upsert {written} 点")


if __name__ == "__main__":
    main()
