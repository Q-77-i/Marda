"""真库报告读取（P1-M12）：评测的语料入口，**只读**。

`app/db.py` 的 `list_reports` 按 user_id 取数（面向 FR-19 的单个用户），评测要的是
「全部技术面报告」这个横切面，故在此单独读——但只做 `mode=ro` 打开，评测永远不写库。
行为面报告排除在外：`/recommendations` 对行为面直接返回空分组（P1-M11 决策 ⑤），
检索 golden 与 RAGAS 都只关心技术面。会话 2 的评分 golden 反过来**要**行为面样本
（`answer_samples`），故读取函数按类型参数化。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.domain import INTERVIEW_BEHAVIORAL, INTERVIEW_TECH


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


def _bank_key_points(con: sqlite3.Connection, question_ids: set[str]) -> dict[str, list[str]]:
    """题库关键点（批量一次查完）。生成题不在表里 → 由调用方退回评分记录里的并集。"""
    if not question_ids:
        return {}
    marks = ",".join("?" for _ in question_ids)
    rows = con.execute(
        f"SELECT id, key_points FROM questions WHERE id IN ({marks})", sorted(question_ids)
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        try:
            points = json.loads(row["key_points"] or "[]")
        except json.JSONDecodeError:  # 库里就是坏 JSON：宁可当场炸，不静默给空表
            raise ValueError(f"questions.key_points 不是合法 JSON（id={row['id']}）") from None
        out[row["id"]] = [str(p) for p in points]
    return out


def answer_samples(db_path: Path, *, interview_type: str = INTERVIEW_TECH) -> list[dict[str, Any]]:
    """历史回答样本（评分 golden 的「真库臂」语料）。

    每条 = 一次**最终评分任务**的输入与当时输出：题干 + 关键点 + 合并后的回答
    （首答 + 追问补充，与评分官当时看到的一致）+ 当时实得五维。

    - 关键点优先取**题库原表**（canonical，即便评分官漏判过某条也完整）；
      生成题（`question_id` 为空）退回评分记录里的 covered+missed 并集；
    - 「当时实得」（`overall`）**只作人审参照**，不当基准——它是评分官自己的输出，
      拿它当基准等于自己给自己打分（见 `judge_golden` 模块注释）。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT interview_id, payload FROM reports ORDER BY created_at ASC, interview_id ASC"
        ).fetchall()
        payloads = []
        for row in rows:
            payload = json.loads(row["payload"])
            # 老场次无 interview_type（M11 前）≡ 技术面
            if (payload.get("interview_type") or INTERVIEW_TECH) != interview_type:
                continue
            payloads.append({"interview_id": row["interview_id"], "payload": payload})
        bank_ids = {
            str(row.get("question_id"))
            for row in payloads
            for row in row["payload"].get("per_question_comments") or []
            if row.get("question_id")
        }
        bank = _bank_key_points(con, bank_ids)
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    for entry in payloads:
        for row in entry["payload"].get("per_question_comments") or []:
            answer = (row.get("candidate_answer") or "").strip()
            score = row.get("score") or {}
            if not answer or not score:
                continue
            qid = str(row.get("question_id") or "")
            covered = [str(p) for p in row.get("covered_key_points") or []]
            missed = [str(p) for p in row.get("missed_key_points") or []]
            if qid and qid in bank:
                key_points, source = bank[qid], "bank"
            else:
                key_points, source = covered + missed, "score"
            out.append({
                "interview_id": entry["interview_id"],
                "question_id": qid or None,
                "question": row.get("text") or "",
                "key_points": [p for p in key_points if p.strip()],
                "answer": answer,
                "overall": round(sum(score.values()) / len(score), 4),
                "domain": row.get("domain"),
                "key_points_from": source,
            })
    return out
