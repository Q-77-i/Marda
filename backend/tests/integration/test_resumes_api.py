"""简历链路集成测试（P2-M11 FR-28）：解析端点 + 创建带简历 + 图内三处注入 + 引用计数清理。

**零回归是本文件的重点之一**：无简历时三处 prompt 与接入前**逐字一致**——这里用
「prompt 等于模板原样」直接钉死（只断言「没出现那句话」证明不了逐字一致）。
"""

from __future__ import annotations

import json

import pytest

from app import db, llm
from app.agents.prompts import (
    ASK_BANK_TEMPLATE,
    ASK_RESUME_NOTE,
    INTRO_RESUME_NOTE,
    INTRO_TEMPLATE,
    PROFILE_RESUME_BLOCK,
    PROFILE_TEMPLATE,
    TECH_PERSONA,
)
from app.graph.rules.transition import estimated_minutes
from fake_llm import DEFAULT_GENERATED, DEFAULT_RESUME

RESUME_MD = "# 张三的简历\n\n## 项目经历\n- 多轮检索问答系统：负责检索链路与重排。\n"

# 三处 prompt 的稳定字面标记（模板原文，不含占位符）
_INTRO_MARKER = "每轮后你会根据回答选择追问或换题"
_PROFILE_MARKER = "从候选人的自我介绍中"
_ASK_MARKER = "用面试官口吻向候选人提出下面这道题"


def _files(content: str = RESUME_MD, filename: str = "简历.md"):
    return {"file": (filename, content.encode("utf-8"), "text/markdown")}


async def _events(response) -> list[dict]:
    """逐行解析 SSE 流为事件列表（与 test_api 同一套最小解析）。"""
    out, current = [], {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append(current)
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    if current:
        out.append(current)
    return out


async def _upload(client, **kwargs) -> dict:
    r = await client.post("/api/resumes", files=_files(**kwargs))
    assert r.status_code == 201, r.text
    return r.json()


async def _create(client, question_count: int = 2, **extra) -> tuple[str, list[dict]]:
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": question_count, **extra},
    ) as r:
        assert r.status_code == 200
        events = await _events(r)
    return events[0]["data"]["interview_id"], events


async def _send(client, interview_id: str, content: str) -> list[dict]:
    async with client.stream(
        "POST", f"/api/interviews/{interview_id}/messages", json={"content": content},
    ) as r:
        assert r.status_code == 200
        return await _events(r)


# ---- 解析端点 ----


async def test_上传文件解析成功_只回结构化结果(client):
    body = await _upload(client)

    assert body["resume_id"].startswith("r_")
    assert body["filename"] == "简历.md"
    assert body["projects"] == DEFAULT_RESUME["projects"]  # fake 抽取结果（真链路走 flash）
    assert body["skills"] == DEFAULT_RESUME["skills"]
    assert body["chars"] == len(RESUME_MD.strip())
    assert "text" not in body, "接口只回结构化结果，不回原文"

    row = db.get_resume(
        llm.get_settings().db_path, user_id=_user_id(), resume_id=body["resume_id"]
    )
    assert row is not None
    assert row["text"] == RESUME_MD.strip()  # 原文留在库里（供重新解析）
    assert row["parsed"]["projects"] == DEFAULT_RESUME["projects"]


async def test_粘贴文本走同一端点(client):
    r = await client.post("/api/resumes", data={"text": " 我是张三，做过检索系统。 "})

    assert r.status_code == 201
    assert r.json()["filename"] == "粘贴文本"
    assert r.json()["chars"] == len("我是张三，做过检索系统。")


async def test_校验_二选一_后缀_大小(client):
    none = await client.post("/api/resumes")
    assert none.status_code == 400 and "二选一" in none.json()["detail"]

    both = await client.post("/api/resumes", files=_files(), data={"text": "文本"})
    assert both.status_code == 400 and "二选一" in both.json()["detail"]

    suffix = await client.post("/api/resumes", files=_files(filename="简历.docx"))
    assert suffix.status_code == 400 and "pdf" in suffix.json()["detail"]

    big = await client.post(
        "/api/resumes", files=_files(content="填充" * 1_200_000)  # > 2MB
    )
    assert big.status_code == 413


async def test_空内容报错并指路粘贴(client):
    r = await client.post("/api/resumes", files=_files(content="   \n  "))

    assert r.status_code == 400
    assert "粘贴文本" in r.json()["detail"]  # 扫描版 PDF 的出路要写清楚


async def test_模型不可用_502并给出路(client, monkeypatch):
    from app.tools import resumes

    async def _boom(text):
        raise llm.LLMError("上游挂了", retryable=True)

    monkeypatch.setattr(resumes, "parse_resume", _boom)
    r = await client.post("/api/resumes", files=_files())

    assert r.status_code == 502
    assert "粘贴文本" in r.json()["detail"]


async def test_未登录401(anon_client):
    r = await anon_client.post("/api/resumes", files=_files())
    assert r.status_code == 401


# ---- 创建带简历：图内三处注入 ----


async def test_有简历_三处注入都带上(client, fake_llm):
    resume = await _upload(client)
    interview_id, _ = await _create(client, resume_id=resume["resume_id"])
    await _send(client, interview_id, "我是应届生，做过 RAG 项目")

    intro = _system(fake_llm, _INTRO_MARKER)
    assert INTRO_RESUME_NOTE in intro  # 开场白：已看过简历

    profile = _system(fake_llm, _PROFILE_MARKER)
    assert PROFILE_RESUME_BLOCK.split("\n")[1] in profile  # 合并块在位
    assert DEFAULT_RESUME["projects"][0] in profile  # 简历解析结果确实预填进了 candidate_profile

    ask = _system(fake_llm, _ASK_MARKER)
    assert ASK_RESUME_NOTE in ask  # 出题指引在位
    # （FakeLLM 的提炼不真做合并——简历项目经「合并 prompt」留存到出题，由真链路
    #    smoke_resume 断言：项目题必须引用简历里的标识串）


async def test_无简历_三处_prompt_与模板逐字一致(client, fake_llm):
    interview_id, _ = await _create(client)
    await _send(client, interview_id, "我是应届生，做过 RAG 项目")

    intro = _system(fake_llm, _INTRO_MARKER)
    assert intro == INTRO_TEMPLATE.format(
        persona=TECH_PERSONA, position="Agent/AI 工程师", kind="技术模拟面试",
        question_count=2, duration=estimated_minutes(2),
    )

    profile = _system(fake_llm, _PROFILE_MARKER)
    # chat_json 会在 system 末尾追加 JSON Schema 提示（既有行为，见 llm._with_schema_hint）：
    # 模板部分逐字一致即可（split("\n\n")[0] = 模板原文，提示是其后被追加的那段）
    assert profile.split("\n\n")[0] == PROFILE_TEMPLATE.format(
        content="我是应届生，做过 RAG 项目"
    )

    # 出题发生在自我介绍提炼之后，profile 槽已是提炼结果（FakeLLM 的固定值，按
    # profile_node 的拼接口径：summary；项目经历：…）——无简历时逐字等于模板填参
    ask = _system(fake_llm, _ASK_MARKER)
    assert ask == ASK_BANK_TEMPLATE.format(
        persona=TECH_PERSONA, question=DEFAULT_GENERATED["text"],  # 2 题场首题 = 生成的项目深挖题
        profile="应届生，Agent 方向；项目经历：做过 RAG 问答系统",
    )


async def test_他人简历404(client, login_as):
    other = await login_as("bob")
    resume = await _upload(other)

    r = await client.post(
        "/api/interviews",
        json={"position": "x", "question_count": 2, "resume_id": resume["resume_id"]},
    )
    assert r.status_code == 404  # 不泄露存在性（同 M7 口径）

    missing = await client.post(
        "/api/interviews", json={"position": "x", "question_count": 2, "resume_id": "r_nope"}
    )
    assert missing.status_code == 404


# ---- 引用计数清理 ----


async def test_解析新简历清掉旧的孤儿(client):
    """解析成功却没创建场次的简历会留孤儿——下一次解析顺手清掉（不留静默垃圾）。

    有场次引用的简历不受影响（那是「正在用」的资产）。
    """
    db_path = llm.get_settings().db_path
    user_id = _user_id()
    used = await _upload(client, content="# 简历 A\n用过的那份")
    await _create(client, resume_id=used["resume_id"])

    abandoned = await _upload(client, content="# 简历 B\n解析后没建场次")
    assert db.get_resume(db_path, user_id=user_id, resume_id=abandoned["resume_id"]) is not None

    latest = await _upload(client, content="# 简历 C\n再一次解析")
    assert db.get_resume(db_path, user_id=user_id, resume_id=abandoned["resume_id"]) is None, (
        "没人引用的旧简历应被清掉"
    )
    assert db.get_resume(db_path, user_id=user_id, resume_id=used["resume_id"]) is not None, (
        "被场次引用的简历不能清"
    )
    assert db.get_resume(db_path, user_id=user_id, resume_id=latest["resume_id"]) is not None


async def test_删场次按引用计数清理简历(client):
    resume = await _upload(client)
    db_path = llm.get_settings().db_path
    first, _ = await _create(client, resume_id=resume["resume_id"])
    second, _ = await _create(client, resume_id=resume["resume_id"])

    assert await client.delete(f"/api/interviews/{first}") is not None
    assert db.get_resume(db_path, user_id=_user_id(), resume_id=resume["resume_id"]) is not None, (
        "还有场次引用它，简历不能删"
    )

    await client.delete(f"/api/interviews/{second}")
    assert db.get_resume(db_path, user_id=_user_id(), resume_id=resume["resume_id"]) is None


def _user_id() -> str:
    return db.get_user_by_username(llm.get_settings().db_path, "alice")["id"]


def _system(fake, marker: str) -> str:
    """取最近一次包含标记的 system prompt（FakeLLM 全量留档）。"""
    hits = [c["system"] for c in fake.calls if marker in c["system"]]
    assert hits, f"没有找到含「{marker}」的调用"
    return hits[-1]
