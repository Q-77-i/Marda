"""业务库门面（SPEC §8）：按后端分派 SQLite / PostgreSQL。

**分派规则只有一个**：第一个参数 `target` 的类型决定后端——
- `Path` → SQLite（单机 / 单测；题库也在这条路径上）
- `str`（`postgresql://…`）→ PostgreSQL（生产）

调用方统一传 `settings.business_db`（见 config.Settings.business_db），
**路由层不出现 SQL 方言分支**——这是升级方案 §4.2.3 的硬要求：
方言差异全部关在 db_sqlite / db_postgres 两个实现里。

函数签名与语义在两个后端上**必须一致**（db_sqlite / db_postgres 同名方法），
否则门面就成了「同一函数两种行为」的陷阱——改动时两边一起改，并跑同一套用例。

题库（questions / question_sources）**不迁 PG**：它是离线构建、运行时只读的资产，
检索侧 join 仍走 SQLite（升级方案 §3.1）。所以 `settings.db_path` 的含义不变：
「SQLite 文件（题库 + SQLite 形态下的业务库）」。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import db_postgres, db_sqlite
from app.domain import INTERVIEW_TECH

# 后端实现必须提供的公共方法（两个 store 都要有，缺一个就是接口漂移）
_STORE_METHODS = (
    "ensure_schema",
    "create_user",
    "get_user",
    "get_user_by_username",
    "claim_orphan_interviews",
    "create_interview",
    "finish_interview",
    "save_answers",
    "save_report",
    "finalize_interview",
    "get_interview",
    "delete_interview",
    "query_interviews",
    "list_reports",
    "count_finished_without_report",
    "save_resume",
    "prune_unreferenced_resumes",
    "get_resume",
    "get_report",
)


def _now() -> str:
    """UTC ISO 时间戳（微秒精度，同秒多场次排序不丢序）。

    **时钟的单一来源**：两个后端实现都从这里取时间（门面在每次取 store 时把本函数
    注入进去），单测 monkeypatch `db._now` 就能控制落库时间戳。
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _store(target: Path | str):
    """取后端实现。Path → SQLite；str（DSN）→ PostgreSQL。"""
    if isinstance(target, str):
        store = db_postgres.get_store(target)
        store.clock = _now  # 每次取都同步一次时钟（单测会 monkeypatch db._now）
        return store
    return db_sqlite.SqliteStore(Path(target), clock=_now)


# ---------- schema ----------


def ensure_schema(target: Path | str) -> None:
    """建表 + 补列（幂等）。生产环境建议在部署流程里跑一次，不必每次启动都跑。"""
    _store(target).ensure_schema()


# ---------- users ----------


def create_user(
    target: Path | str, *, user_id: str, username: str, password_hash: str
) -> bool:
    """建账号；用户名已存在（UNIQUE 冲突）返回 False。username 由调用方保证已小写化。"""
    return _store(target).create_user(
        user_id=user_id, username=username, password_hash=password_hash
    )


def get_user(target: Path | str, user_id: str) -> dict | None:
    return _store(target).get_user(user_id)


def get_user_by_username(target: Path | str, username: str) -> dict | None:
    return _store(target).get_user_by_username(username)


def claim_orphan_interviews(target: Path | str, user_id: str) -> int:
    """无归属场次（user_id IS NULL）归入该用户，返回认领数。"""
    return _store(target).claim_orphan_interviews(user_id)


# ---------- interviews ----------


def create_interview(
    target: Path | str,
    *,
    interview_id: str,
    position: str,
    question_count: int,
    difficulty: str = "L1",
    user_id: str | None = None,
    interview_type: str = INTERVIEW_TECH,
    resume_id: str | None = None,
) -> None:
    """`difficulty` 存创建时选定的难度；`interview_type` = tech / behavioral。"""
    _store(target).create_interview(
        interview_id=interview_id,
        position=position,
        question_count=question_count,
        difficulty=difficulty,
        user_id=user_id,
        interview_type=interview_type,
        resume_id=resume_id,
    )


def finish_interview(target: Path | str, interview_id: str) -> None:
    _store(target).finish_interview(interview_id)


def save_answers(target: Path | str, interview_id: str, records: list[dict[str, Any]]) -> None:
    """records 字段：question_id/domain/difficulty/candidate_answer/followup_count/
    skipped/score_json（question_id 为空 = 生成题，SPEC §8）。"""
    _store(target).save_answers(interview_id, records)


def save_report(target: Path | str, interview_id: str, payload: dict[str, Any]) -> None:
    """id = interview_id（1:1），二次保存自然覆盖（幂等）。"""
    _store(target).save_report(interview_id, payload)


def finalize_interview(
    target: Path | str,
    interview_id: str,
    records: list[dict[str, Any]],
    report: dict[str, Any],
) -> None:
    """**单事务**收尾（P4-E2）：answers + report + interviews.status 一起成或一起败。

    旧路径三次独立写，中途失败会留下「图 finished、业务 running、报告缺失」的半成品场次。
    """
    _store(target).finalize_interview(interview_id, records, report)


def get_interview(target: Path | str, interview_id: str) -> dict | None:
    return _store(target).get_interview(interview_id)


def delete_interview(target: Path | str, interview_id: str) -> bool:
    """物理删除场次：interviews/answers/reports 三表 + 引用计数清理简历。"""
    return _store(target).delete_interview(interview_id)


def query_interviews(
    target: Path | str,
    *,
    user_id: str,
    status: str | None = None,
    interview_type: str | None = None,
    since: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> tuple[list[dict], int, dict[str, int]]:
    """分页 + 过滤的历史列表：返回 (当前页行, 过滤后总数, 状态分布)。user_id 必传。"""
    return _store(target).query_interviews(
        user_id=user_id,
        status=status,
        interview_type=interview_type,
        since=since,
        page=page,
        page_size=page_size,
    )


def list_reports(target: Path | str, *, user_id: str, limit: int = 200) -> list[dict]:
    """用户全部已落库报告 + 场次元信息，按开始时间升序。"""
    return _store(target).list_reports(user_id=user_id, limit=limit)


def count_finished_without_report(target: Path | str, *, user_id: str) -> int:
    """本人已完成但没有报告的场次数。"""
    return _store(target).count_finished_without_report(user_id=user_id)


# ---------- resumes ----------


def save_resume(
    target: Path | str,
    *,
    resume_id: str,
    user_id: str,
    filename: str,
    text: str,
    parsed: dict[str, Any],
) -> None:
    """落一份简历：原文 + 结构化结果。"""
    _store(target).save_resume(
        resume_id=resume_id, user_id=user_id, filename=filename, text=text, parsed=parsed
    )


def prune_unreferenced_resumes(target: Path | str, *, user_id: str) -> int:
    """清掉该用户没有任何场次引用的旧简历，返回删除行数。"""
    return _store(target).prune_unreferenced_resumes(user_id=user_id)


def get_resume(target: Path | str, *, user_id: str, resume_id: str) -> dict | None:
    """取本人简历；非本人 / 不存在一律 None。"""
    return _store(target).get_resume(user_id=user_id, resume_id=resume_id)


def get_report(target: Path | str, interview_id: str) -> dict | None:
    return _store(target).get_report(interview_id)
