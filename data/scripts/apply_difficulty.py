"""把难度重标注表应用到**已落库**的题库（SQLite + Qdrant），幂等可重跑。

正常路径是重跑管线（`combine.py` 会带上标注）；本脚本是**不重建向量库**的补齐手段——
只为档位变化把 1096 个向量点重嵌一遍不值当（标注不改 doc 文本，向量本身没变）。
Qdrant 的 `difficulty` 是出题检索的过滤字段（SPEC §5），payload 不跟着改，
题就会按旧档被抽中或漏抽。

语义与 combine 同源（`bank.apply_difficulty`），故两条路径结果一致；跑完再跑一次应为零改动。

用法：python data/scripts/apply_difficulty.py [--difficulty 路径] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from bank import apply_difficulty, load_difficulty
from qdrant_client import models as qm

from app.config import get_settings
from app.tools.question_search import COLLECTION, get_qdrant_client

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIFFICULTY = REPO_ROOT / "data" / "curation" / "difficulty_annotations.json"
CHUNK = 500  # IN 子句与 Qdrant MatchAny 都分批，别把 1000+ id 塞进一条语句


def _distribution(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT difficulty, COUNT(*) FROM questions WHERE status='enabled' AND user_id IS NULL"
            " GROUP BY difficulty"
        ).fetchall()
    return {level: count for level, count in rows}


def apply_to_sqlite(
    db_path: Path, levels: dict[str, str], *, dry_run: bool
) -> tuple[list[dict], list[str]]:
    """按标注表改 SQLite，返回（改动明细，未命中的条目 id）。明细含原档位与新档位。"""
    changes: list[dict] = []
    unmatched: list[str] = []
    ids = list(levels)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            chunk_levels = {qid: levels[qid] for qid in chunk}  # 未命中按分块判，别把整表算进去
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, difficulty FROM questions WHERE id IN ({placeholders})", chunk
            ).fetchall()
            questions = [{"question_id": row["id"], "difficulty": row["difficulty"]} for row in rows]
            unmatched += apply_difficulty(questions, chunk_levels)
            changes += [
                {"question_id": q["question_id"], "level": q["difficulty"], "was": before}
                for q, before in zip(
                    questions,
                    (row["difficulty"] for row in rows),
                    strict=True,
                )
                if q["difficulty"] != before
            ]
        if not dry_run and changes:
            conn.executemany(
                "UPDATE questions SET difficulty=? WHERE id=?",
                [(c["level"], c["question_id"]) for c in changes],
            )
    return changes, sorted(unmatched)


async def apply_to_qdrant(changes: list[dict], *, dry_run: bool) -> int:
    """向量库跟标注对齐（改动点 set_payload 新档位），返回触及的点数。"""
    if not changes:
        return 0
    client = get_qdrant_client()
    by_level: dict[str, list[str]] = {}
    for change in changes:
        by_level.setdefault(change["level"], []).append(change["question_id"])

    touched = 0
    for level, ids in sorted(by_level.items()):
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            selector = qm.Filter(
                must=[qm.FieldCondition(key="question_id", match=qm.MatchAny(any=chunk))]
            )
            if dry_run:
                found, _ = await client.scroll(
                    collection_name=COLLECTION, scroll_filter=selector, limit=len(chunk),
                    with_payload=False,
                )
                print(f"  [预演] {level}：将改 {len(found)} 点 payload")
            else:
                await client.set_payload(
                    collection_name=COLLECTION, payload={"difficulty": level},
                    points=selector, wait=True,
                )
            touched += len(chunk)
    return touched


async def main() -> None:
    parser = argparse.ArgumentParser(description="把难度重标注表应用到已落库的题库")
    parser.add_argument("--difficulty", type=Path, default=DEFAULT_DIFFICULTY)
    parser.add_argument("--dry-run", action="store_true", help="只打印将发生的改动")
    args = parser.parse_args()

    levels = load_difficulty(args.difficulty)
    print(f"难度重标注表 {args.difficulty.name}：{len(levels)} 条")
    if not levels:
        return

    db_path = get_settings().db_path
    before = _distribution(db_path)
    changes, unmatched = apply_to_sqlite(db_path, levels, dry_run=args.dry_run)
    for change in changes[:15]:
        print(f"  {change['question_id']} {change['was']} → {change['level']}")
    if len(changes) > 15:
        print(f"  …（共 {len(changes)} 处改动）")
    if unmatched:
        print(f"  ⚠ 未命中 {len(unmatched)} 条（题干改过？条目已失效）：{', '.join(unmatched[:10])}")

    touched = await apply_to_qdrant(changes, dry_run=args.dry_run)
    after = _distribution(db_path)
    print(f"\nSQLite 难度分布：{dict(sorted(before.items()))} → {dict(sorted(after.items()))}"
          f"（{'预演，未写入' if args.dry_run else '已写入'}）")
    print(f"Qdrant：触及 {touched} 点"
          f"（{'预演' if args.dry_run else '已写入'}，改动 {len(changes)} 处）")


if __name__ == "__main__":
    asyncio.run(main())
