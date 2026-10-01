"""真库报告读取（P1-M12）：评测的语料入口，**只读**。

`app/db.py` 的 `list_reports` 按 user_id 取数（面向 FR-19 的单个用户），评测要的是
「全部技术面报告」这个横切面，故在此单独读——但只做 `mode=ro` 打开，评测永远不写库。
行为面报告排除在外：`/recommendations` 对行为面直接返回空分组（P1-M11 决策 ⑤），
检索 golden 与 RAGAS 都只关心技术面。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.domain import INTERVIEW_BEHAVIORAL


def tech_report_payloads(db_path: Path) -> list[dict[str, Any]]:
    """全部技术面报告（`[{interview_id, payload}]`，按报告落库时间升序）。

    老场次没有 `interview_type` 字段（M11 之前）→ 视为技术面（同 M11「NULL ≡ tech」口径）。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT interview_id, payload FROM reports ORDER BY created_at ASC, interview_id ASC"
        ).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("interview_type") == INTERVIEW_BEHAVIORAL:
            continue
        out.append({"interview_id": row["interview_id"], "payload": payload})
    return out
