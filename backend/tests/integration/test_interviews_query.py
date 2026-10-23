"""面试记录列表的分页与过滤（P2 面试记录页）：GET /api/interviews 查询参数。

覆盖：分页边界（page/page_size/越界页）、三个过滤条件（status/type/since）、
total 与 counts 随过滤、非法参数 422、同秒场次的稳定排序。

数据准备直接落库（db.create_interview + SQL 改状态/时间/类型），不为列表查询
跑完整场次——FakeLLM 的开场与评分对读路径没有贡献。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from app import db
from app.config import get_settings


def _user_id() -> str:
    with sqlite3.connect(get_settings().db_path) as conn:
        row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    assert row is not None, "client fixture 已登录，users 表里应有用户"
    return row[0]


def _insert(
    *,
    interview_type: str = "tech",
    started_at: datetime | None = None,
) -> str:
    interview_id = uuid.uuid4().hex
    db.create_interview(
        get_settings().db_path,
        interview_id=interview_id,
        position="Agent/AI 工程师",
        question_count=2,
        user_id=_user_id(),
        interview_type=interview_type,
    )
    if started_at is not None:
        with sqlite3.connect(get_settings().db_path) as conn:
            conn.execute(
                "UPDATE interviews SET started_at=? WHERE id=?",
                (started_at.isoformat(), interview_id),
            )
    return interview_id


def _finish(interview_id: str) -> None:
    db.finish_interview(get_settings().db_path, interview_id)


def _null_type(interview_id: str) -> None:
    """模拟老场次：interview_type 为 NULL（P1-M11 之前建的）。"""
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.execute("UPDATE interviews SET interview_type=NULL WHERE id=?", (interview_id,))


async def test_分页边界(client):
    ids = [_insert() for _ in range(3)]

    body = (await client.get("/api/interviews", params={"page": 1, "page_size": 2})).json()
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2

    page2 = (await client.get("/api/interviews", params={"page": 2, "page_size": 2})).json()
    assert len(page2["items"]) == 1 and page2["total"] == 3
    # 两页拼起来 = 全集，无重复无遗漏
    assert {x["id"] for x in body["items"] + page2["items"]} == set(ids)

    empty = (await client.get("/api/interviews", params={"page": 3, "page_size": 2})).json()
    assert empty["items"] == [] and empty["total"] == 3


async def test_默认分页参数(client):
    _insert()
    body = (await client.get("/api/interviews")).json()
    assert body["page"] == 1 and body["page_size"] == 10


async def test_按状态过滤_计数随过滤(client):
    running = _insert()
    finished = _insert()
    _finish(finished)

    all_body = (await client.get("/api/interviews")).json()
    assert all_body["counts"] == {"finished": 1, "running": 1}
    assert all_body["total"] == 2

    body = (await client.get("/api/interviews", params={"status": "running"})).json()
    assert [x["id"] for x in body["items"]] == [running]
    assert body["total"] == 1
    # counts 基于过滤后的集合（与 total 同源），不是全表分布
    assert body["counts"] == {"finished": 0, "running": 1}

    body = (await client.get("/api/interviews", params={"status": "finished"})).json()
    assert [x["id"] for x in body["items"]] == [finished]
    assert body["counts"] == {"finished": 1, "running": 0}


async def test_按类型过滤_老场次按技术面(client):
    tech = _insert(interview_type="tech")
    behavioral = _insert(interview_type="behavioral")
    legacy = _insert(interview_type="tech")
    _null_type(legacy)  # 老库补列后为 NULL，产品口径按技术面处理

    body = (await client.get("/api/interviews", params={"type": "tech"})).json()
    assert {x["id"] for x in body["items"]} == {tech, legacy}

    body = (await client.get("/api/interviews", params={"type": "behavioral"})).json()
    assert [x["id"] for x in body["items"]] == [behavioral]


async def test_按开始时间过滤(client):
    now = datetime.now(timezone.utc)
    recent = _insert(started_at=now)
    old = _insert(started_at=now - timedelta(days=40))

    since = (now - timedelta(days=30)).date().isoformat()
    body = (await client.get("/api/interviews", params={"since": since})).json()
    assert [x["id"] for x in body["items"]] == [recent]
    assert body["total"] == 1
    assert old not in {x["id"] for x in body["items"]}


async def test_组合过滤(client):
    now = datetime.now(timezone.utc)
    hit = _insert(interview_type="behavioral", started_at=now)
    _insert(interview_type="behavioral", started_at=now - timedelta(days=40))
    _finish(hit)

    since = (now - timedelta(days=7)).date().isoformat()
    body = (
        await client.get(
            "/api/interviews",
            params={"status": "finished", "type": "behavioral", "since": since},
        )
    ).json()
    assert [x["id"] for x in body["items"]] == [hit]
    assert body["total"] == 1


async def test_非法参数422(client):
    bad_params = [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 51},
        {"status": "unknown"},
        {"type": "hr"},
        {"since": "not-a-date"},
    ]
    for params in bad_params:
        r = await client.get("/api/interviews", params=params)
        assert r.status_code == 422, params


async def test_同秒场次排序稳定(client):
    # 同一 started_at 的三场：靠 id ASC 兜底，分页两次不重不漏
    same = datetime.now(timezone.utc)
    ids = {_insert(started_at=same) for _ in range(3)}

    first = (await client.get("/api/interviews", params={"page": 1, "page_size": 2})).json()
    second = (await client.get("/api/interviews", params={"page": 2, "page_size": 2})).json()
    got = [x["id"] for x in first["items"] + second["items"]]
    assert len(got) == 3 and set(got) == ids
