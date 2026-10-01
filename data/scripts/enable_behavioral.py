"""启用行为面题库（P1-M11 FR-22）：SQLite status 翻转 + Qdrant 补点，幂等可重跑。

行为题自 M5 起以 draft 入库（域当时未启用），M9 又把 8 道项目叙事题改判进该域——
M11 起行为面场次要从这个池子出题，故需要一次性把**有实质答案**的行为题翻成 enabled
并补进向量库（draft 只进 SQLite，不进 Qdrant）。

为什么不让用户跑整条管线（parse → combine → enrich → ingest）：那会全量重建 1085 个
向量点，只为十来道题不值当，且盘上产物还滞后于真库（见 CLAUDE.md 待办）。
本脚本与 apply_overrides.py 同一模式：**就地补齐**，语义与管道同源（复用 `finalize_status`
与 ingest 的点构造），故将来整链重跑的结果与它一致（管线的入库域已含行为面）。

**验收口径（D6）**：不能只看 status 翻了没——脚本最后从 Qdrant 把该域的点**读回来核对**
（数量 + 逐点 id），写不进去会直接报错退出。

用法：docker compose up -d embedding && python data/scripts/enable_behavioral.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
import httpx
from bank import finalize_status
from ingest import COLLECTION, DENSE, SPARSE, VECTOR_SIZE, _qdrant_payload, point_id
from qdrant_client import AsyncQdrantClient, models as qm

from app.config import get_settings
from app.domain import BEHAVIORAL_DOMAIN
from app.tools.embedding import EmbeddingClient, question_doc_text, to_sparse_vector

_FIELDS = (
    "id, question, answer, key_points, follow_ups, domain, topic, difficulty,"
    " company, round, source, status"
)


def load_behavioral(db_path: Path) -> list[dict]:
    """公共行为题全量（含 draft：要判断哪些够格 enabled）。"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_FIELDS} FROM questions WHERE domain=? AND user_id IS NULL",
            (BEHAVIORAL_DOMAIN,),
        ).fetchall()
    return [dict(row) for row in rows]


def plan_changes(rows: list[dict]) -> list[dict]:
    """逐题算出目标 status（与管道同一规则 finalize_status），返回需要改动的行。"""
    changes = []
    for row in rows:
        target = {"domain": row["domain"], "answer": row["answer"], "status": "enabled"}
        finalize_status(target)
        if target["status"] != row["status"]:
            changes.append({**row, "target_status": target["status"]})
    return changes


def apply_to_sqlite(db_path: Path, changes: list[dict], *, dry_run: bool) -> None:
    if dry_run or not changes:
        return
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "UPDATE questions SET status=? WHERE id=?",
            [(c["target_status"], c["id"]) for c in changes],
        )
        # 来源明细的 status 跟着题目走（question_sources.status = 该来源答案是否可用）
        conn.executemany(
            "UPDATE question_sources SET status=? WHERE question_id=?",
            [(c["target_status"], c["id"]) for c in changes],
        )


async def sync_qdrant(enabled: list[dict], disabled: list[str], *, dry_run: bool) -> int:
    """补点（enabled）与撤点（disabled），返回写入的点数。

    点构造与 ingest.write_qdrant 同源（doc 文本 = 题干 + 关键点；dense + sparse 双向量；
    确定性 point id）——两处不一致会让 upsert 出的点与全量重建的版本不同。
    """
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
    try:
        if disabled:
            selector = qm.Filter(
                must=[qm.FieldCondition(key="question_id", match=qm.MatchAny(any=disabled))]
            )
            if dry_run:
                print(f"  [预演] 将从 Qdrant 撤下 {len(disabled)} 点")
            else:
                await client.delete(
                    collection_name=COLLECTION, points_selector=selector, wait=True
                )

        if not enabled:
            return 0
        embedder = EmbeddingClient(settings.embedding_url)
        vectors = await embedder.embed([
            question_doc_text(q["question"], json.loads(q["key_points"]) if q["key_points"] else [])
            for q in enabled
        ])
        if len(vectors) != len(enabled):
            raise RuntimeError(f"向量数 {len(vectors)} 与题目数 {len(enabled)} 不一致")
        points = []
        for row, vector in zip(enabled, vectors, strict=True):
            if len(vector.dense) != VECTOR_SIZE:
                raise RuntimeError(f"{row['id']} dense 维度 {len(vector.dense)} != {VECTOR_SIZE}")
            item = {
                "question_id": row["id"],
                "domain": row["domain"],
                "topic": row["topic"],
                "difficulty": row["difficulty"],
                "company": row["company"],
                "round": row["round"],
            }
            points.append(
                qm.PointStruct(
                    id=point_id(row["id"]),
                    vector={DENSE: vector.dense, SPARSE: to_sparse_vector(vector.sparse)},
                    payload=_qdrant_payload(item),
                )
            )
        if dry_run:
            print(f"  [预演] 将向 Qdrant 写入 {len(points)} 点")
        else:
            await client.upsert(collection_name=COLLECTION, points=points, wait=True)
        return len(points)
    finally:
        await client.close()


async def verify_qdrant(expected_ids: set[str]) -> None:
    """从 Qdrant 读回该域的点逐点核对（D6：不能只 flip status）。"""
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
    try:
        records, _ = await client.scroll(
            collection_name=COLLECTION,
            scroll_filter=qm.Filter(
                must=[qm.FieldCondition(key="domain", match=qm.MatchValue(value=BEHAVIORAL_DOMAIN))]
            ),
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
        found = {r.payload.get("question_id") for r in records if r.payload}
        missing = expected_ids - found
        extra = found - expected_ids
        if missing or extra:
            raise SystemExit(
                f"Qdrant 核对失败：缺 {sorted(missing)}，多出 {sorted(extra)}"
                f"（enabled 行为题 {len(expected_ids)} 道，库内该域点 {len(found)} 个）"
            )
        print(f"Qdrant 核对通过：域名 {BEHAVIORAL_DOMAIN} 的点 {len(found)} 个，与 enabled 行为题逐点一致")
    finally:
        await client.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="启用行为面题库（status 翻转 + Qdrant 补点）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将发生的改动")
    args = parser.parse_args()

    db_path = get_settings().db_path
    rows = load_behavioral(db_path)
    changes = plan_changes(rows)
    print(f"公共行为题 {len(rows)} 道；需改动 {len(changes)} 道")
    for change in changes:
        print(
            f"  {change['id']} {change['status']} → {change['target_status']}"
            f"  「{change['question'][:38]}」"
        )

    apply_to_sqlite(db_path, changes, dry_run=args.dry_run)

    # 目标 enabled 集合 = 现有 enabled（未变动的） ∪ 本次翻成 enabled 的
    enabled = [
        row for row in rows
        if row["status"] == "enabled" or any(
            c["id"] == row["id"] and c["target_status"] == "enabled" for c in changes
        )
    ]
    disabled = [c["id"] for c in changes if c["target_status"] != "enabled"]
    try:
        written = await sync_qdrant(enabled, disabled, dry_run=args.dry_run)
    except httpx.TransportError as exc:  # 最常见的失手：忘了起嵌入服务
        raise SystemExit(
            f"嵌入服务不可达（{get_settings().embedding_url}）：先 docker compose up -d embedding"
        ) from exc

    verb = "预演，未写入" if args.dry_run else "已写入"
    print(f"Qdrant：upsert {written} 点、撤点 {len(disabled)} 个（{verb}）")
    if not args.dry_run:
        await verify_qdrant({row["id"] for row in enabled})


if __name__ == "__main__":
    asyncio.run(main())
