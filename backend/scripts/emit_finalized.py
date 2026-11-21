"""P5-J5 回填 / 补发：从已落库报告构建 `interview.finalized` 并写入 outbox。

用途：
1. 线上 finalize 的事件构建失败（报告已落库）→ 手工补发；
2. 演示 / 验证：不重跑面试、不花 LLM 钱，把一场已结束的报告送进事件链；
3. 旧场次按需回填（**不自动跑全量**）。

用法：
  backend/.venv/bin/python backend/scripts/emit_finalized.py --interview-id <id>
  backend/.venv/bin/python backend/scripts/emit_finalized.py --interview-id <id> --dry-run

前置：与 api 同一份 .env（读 business_db）；PG 形态显式给
`DB_BACKEND=postgres DATABASE_URL=postgresql://.../marda`；relay 必须已启动才会发布。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, db_postgres  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.outbox.events import build_interview_finalized  # noqa: E402


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="从已落库报告补发 interview.finalized")
    parser.add_argument("--interview-id", required=True)
    parser.add_argument("--dry-run", action="store_true", help="只打印事件，不写 outbox")
    args = parser.parse_args()

    settings = get_settings()
    target = settings.business_db
    interview = db.get_interview(target, args.interview_id)
    if interview is None:
        print(f"❌ 场次不存在：{args.interview_id}", file=sys.stderr)
        return 1
    report_row = db.get_report(target, args.interview_id)
    if report_row is None:
        print(f"❌ 场次没有落库报告：{args.interview_id}", file=sys.stderr)
        return 1

    report = dict(report_row["payload"])
    # 老报告可能缺事件需要的元信息；以 interviews 行为准补齐（不改库里的 payload）。
    report.setdefault("position", interview.get("position"))
    report.setdefault("interview_type", interview.get("interview_type") or "tech")

    event = build_interview_finalized(
        interview_id=args.interview_id,
        user_id=interview.get("user_id") or "",
        report=report,
        finished_at=_parse_time(interview.get("ended_at")),
    )
    if event is None:
        print(
            "❌ 该报告没有可发事件（无归属用户 / 整场未评分）。缺数据 ≠ 0 分，不伪造事件。",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0

    inserted = db.enqueue_outbox(target, event)
    state = "已入队" if inserted else "已存在（幂等，不重复入队）"
    print(f"event_id={event['event_id']} type={event['type']} {state}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        # 门面缓存了 PG 连接池；脚本退出前显式关掉，避免 psycopg 在解释器退出时刷
        # "couldn't stop thread ... within 5.0 seconds" 的噪音。
        db_postgres.close_all()
