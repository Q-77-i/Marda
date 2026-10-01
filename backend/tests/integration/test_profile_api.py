"""能力档案接口集成测试（FR-19 / SPEC §7）：`GET /api/profile`。

走 ASGI 全链路（FakeLLM + tmp 库）：跑完真场次产生报告 → 档案读回来对账。
覆盖：零态、与报告 payload 对账、场次元信息、用户隔离、未结束场次不入选。全部离线。
"""

from __future__ import annotations

import json

# 同 test_api：一场 2 轮的完整轮次脚本
TURNS = ["我是应届生，做过 RAG 项目", "第一题回答……", "第一题深挖补充……",
         "第二题回答……", "第二题深挖补充……", "请问团队技术栈？", "晋升路径？"]


async def _finish(client, question_count: int = 2) -> str:
    """跑完一场（含报告落库），返回 interview_id。"""
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": question_count},
    ) as r:
        assert r.status_code == 200
        interview_id = ""
        async for line in r.aiter_lines():
            # 首个事件恒为 meta（test_api 已钉死），从这里取场次 id
            if line.startswith("data: ") and not interview_id:
                interview_id = json.loads(line[len("data: "):])["interview_id"]
    assert interview_id
    for turn in TURNS:
        async with client.stream(
            "POST", f"/api/interviews/{interview_id}/messages", json={"content": turn},
        ) as r:
            assert r.status_code == 200
            async for _ in r.aiter_lines():
                pass
    return interview_id


async def test_未登录401(anon_client):
    assert (await anon_client.get("/api/profile")).status_code == 401


async def test_没有场次时给零态结构(client):
    """空档案是正常状态不是 404：前端据 session_count 渲染空态 + 引导。"""
    r = await client.get("/api/profile")

    assert r.status_code == 200
    data = r.json()
    assert data["sessions"] == []
    assert data["weakness_changes"] == []
    assert data["summary"] == {
        "session_count": 0, "average_overall": 0.0,
        "best": None, "worst": None, "latest_delta": None,
    }


async def test_一场结束后档案与报告对账(client):
    interview_id = await _finish(client)
    report = (await client.get(f"/api/interviews/{interview_id}/report")).json()["report"]

    data = (await client.get("/api/profile")).json()

    assert [s["interview_id"] for s in data["sessions"]] == [interview_id]
    session = data["sessions"][0]
    # 单场：总分与报告页显示的是同一个值（D1 单一来源），域得分/短板原样带出
    assert session["overall"] == report["overall"]
    assert session["scores"] == report["scores"]
    assert session["domain_scores"] == report["domain_scores"]
    assert session["weaknesses"] == report["weaknesses"]
    # 场次元信息：岗位取场次行，难度是创建时选的档（adaptive = 用户的选择）
    assert session["position"] == "Agent/AI 工程师"
    assert session["difficulty"] == "adaptive"
    assert session["question_count"] == 2
    assert session["answered_count"] == 2
    assert session["started_at"]
    assert data["summary"]["session_count"] == 1
    assert data["summary"]["best"] == {"interview_id": interview_id, "overall": report["overall"]}
    assert data["summary"]["latest_delta"] is None  # 单场没有「较上场」


async def test_多场按时间升序且给短板变化(client):
    first = await _finish(client)
    second = await _finish(client)

    data = (await client.get("/api/profile")).json()

    moments = [s["started_at"] for s in data["sessions"]]
    assert moments == sorted(moments)  # 升序 = 曲线从左到右（db 层定序）
    assert [s["interview_id"] for s in data["sessions"]] == [first, second]
    # 变化条目从第二场起（首场无从比较）
    assert len(data["weakness_changes"]) == 1
    change = data["weakness_changes"][0]
    assert set(change) == {"interview_id", "started_at", "new", "persistent", "resolved"}
    assert change["interview_id"] == second
    # 同一套脚本跑两场 → 短板集合相同 → 全是持续项
    weaknesses = sorted(data["sessions"][0]["weaknesses"])
    assert change["persistent"] == weaknesses
    assert change["new"] == [] and change["resolved"] == []
    assert data["summary"]["latest_delta"]["delta"] == 0.0


async def test_未结束场次不进档案(client):
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": 2},
    ) as r:
        assert r.status_code == 200
        async for _ in r.aiter_lines():
            pass

    data = (await client.get("/api/profile")).json()

    assert data["sessions"] == []  # 没有报告 → 没有分数可画


async def test_只看得到自己的场次(login_as):
    alice = await login_as("alice")
    bob = await login_as("bob")
    interview_id = await _finish(alice)

    assert [s["interview_id"] for s in (await alice.get("/api/profile")).json()["sessions"]] == [
        interview_id
    ]
    bob_data = (await bob.get("/api/profile")).json()
    assert bob_data["sessions"] == []
    assert bob_data["summary"]["session_count"] == 0
