"""把人工改判表应用到**已落库**的题库（SQLite + Qdrant），幂等可重跑。

正常路径是重跑管线（`combine.py` 会带上改判）；本脚本是**不进全量重嵌**的补齐手段——
只为十来道题的改判把 1095 个向量点重建一遍不值当。改判语义与 combine 同源
（`bank.apply_overrides`），故两条路径的结果一致。

向量库要跟着 SQLite 走：改判成 draft 的撤点；**仍在 enabled 但换了域**的改 payload——
Qdrant 的 domain 是检索过滤字段（SPEC §8.1），payload 不跟着改就会按旧域被搜出来。

用法：python data/scripts/apply_overrides.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from bank import apply_overrides, load_overrides
from qdrant_client import models as qm

from app.config import get_settings
from app.tools.question_search import COLLECTION, get_qdrant_client

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OVERRIDES = REPO_ROOT / "data" / "curation" / "question_overrides.json"


def _enabled_public(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM questions WHERE status='enabled' AND user_id IS NULL"
        ).fetchone()[0]


def apply_to_sqlite(
    db_path: Path, overrides: dict, *, dry_run: bool
) -> tuple[list[dict], list[str]]:
    """改判并落库，返回（改动明细，未命中的条目 id）。明细含改判后的 domain/status。"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(overrides))
        rows = conn.execute(
            f"SELECT id, question, answer, domain, status FROM questions WHERE id IN ({placeholders})",
            list(overrides),
        ).fetchall()
        records = [dict(row) for row in rows]
        questions = [
            {
                "question_id": row["id"],
                "domain": row["domain"],
                "status": row["status"],
                "answer": row["answer"],
            }
            for row in records
        ]
        unmatched = apply_overrides(questions, overrides)

        changes = []
        for record, question in zip(records, questions, strict=True):
            if (record["domain"], record["status"]) == (question["domain"], question["status"]):
                continue
            changes.append({
                "question_id": record["id"],
                "question": record["question"],
                "domain": question["domain"],
                "status": question["status"],
                "was": f"{record['domain']}/{record['status']}",
            })
        if not dry_run and changes:
            conn.executemany(
                "UPDATE questions SET domain=?, status=? WHERE id=?",
                [(c["domain"], c["status"], c["question_id"]) for c in changes],
            )
    return changes, unmatched


async def apply_to_qdrant(changes: list[dict], *, dry_run: bool) -> tuple[int, int]:
    """向量库跟改判对齐，返回（撤点数，改 payload 域数）。"""
    if not changes:
        return 0, 0
    client = get_qdrant_client()
    to_delete = [c["question_id"] for c in changes if c["status"] != "enabled"]
    to_retag = [c for c in changes if c["status"] == "enabled"]

    if to_delete:
        selector = qm.Filter(
            must=[qm.FieldCondition(key="question_id", match=qm.MatchAny(any=to_delete))]
        )
        if dry_run:
            found, _ = await client.scroll(
                collection_name=COLLECTION, scroll_filter=selector, limit=len(to_delete),
                with_payload=False,
            )
            print(f"  [预演] 将从 Qdrant 撤下 {len(found)} 点")
        else:
            await client.delete(collection_name=COLLECTION, points_selector=selector, wait=True)

    for change in to_retag:  # 仍在库里但换了域：payload 是检索过滤字段，必须跟着改
        await client.set_payload(
            collection_name=COLLECTION,
            payload={"domain": change["domain"]},
            points=qm.Filter(
                must=[qm.FieldCondition(
                    key="question_id", match=qm.MatchValue(value=change["question_id"])
                )]
            ),
            wait=True,
        )
    return len(to_delete), len(to_retag)


async def main() -> None:
    parser = argparse.ArgumentParser(description="把人工改判表应用到已落库的题库")
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--dry-run", action="store_true", help="只打印将发生的改动")
    args = parser.parse_args()

    overrides = load_overrides(args.overrides)
    print(f"改判表 {args.overrides.name}：{len(overrides)} 条")
    if not overrides:
        return

    db_path = get_settings().db_path
    before = _enabled_public(db_path)
    changes, unmatched = apply_to_sqlite(db_path, overrides, dry_run=args.dry_run)
    for change in changes:
        print(f"  {change['question_id']} {change['was']} → {change['domain']}/{change['status']}"
              f"  「{change['question'][:38]}」")
    if unmatched:
        print(f"  ⚠ 未命中 {len(unmatched)} 条（题干改过？条目已失效）：{', '.join(unmatched)}")

    deleted, retagged = await apply_to_qdrant(changes, dry_run=args.dry_run)
    after = _enabled_public(db_path)
    print(f"\nSQLite enabled 公共题：{before} → {after}"
          f"（{'预演，未写入' if args.dry_run else '已写入'}）")
    print(f"Qdrant：撤点 {deleted} 个 / 改域 {retagged} 个")


if __name__ == "__main__":
    asyncio.run(main())
