"""MCP 题库查询 server 的集成测试（P2-M10）：真 SDK、真握手，不联网。

驱动方式 = `Client(mcp)` **in-memory**（SDK v2 支持把 server 实例直接交给客户端，探针实测）：
协议编解码、工具 schema、错误映射都是真的，只是不经过 stdio 进程——真进程由
`scripts/smoke_mcp.py` 走一遍（那条是「可连」的机械证据）。

库 = 集成层 conftest 的 tmp 库（autouse `_test_env`）；题目用共享夹具造，
**私有题、归档题必须不可见**——这个 server 是只读公共出口。
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest
from bank_fixture import create_tables, insert_questions, insert_sources, question_row
from mcp import Client

from app import llm
from app.mcp_server import mcp as server
from app.tools import hybrid_search

TOOL_NAMES = ["browse_questions", "get_question", "search_questions"]
APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def seeded():
    """公共题 2 道（其中一道带来源四要素）+ 私有题 1 道 + 归档题 1 道，种进**本用例的库**。

    库路径必须走 `get_settings().db_path`（集成层 conftest 已把它指到 tmp）——server 是在
    调用时读这个设置的；自己另开一个文件的话，工具读到的是空库（第一次就是这么红的）。
    """
    path = llm.get_settings().db_path
    create_tables(path)
    insert_questions(path, [
        question_row("q_pub1", domain="rag"),
        question_row("q_pub2", domain="memory", difficulty="L3"),
        question_row("q_priv", user_id="u1"),
        question_row("q_archived", status="draft"),
    ])
    insert_sources(path, [("q_pub1", "个人题库", "personal", "https://x/1", "手写", "enabled")])
    return path


async def test_工具面与握手(seeded):
    """三个工具、可被识别；调用真跑通（协议层 + 领域层都动）。"""
    async with Client(server) as client:
        tools = await client.list_tools()

        assert sorted(t.name for t in tools.tools) == TOOL_NAMES
        assert all(t.description for t in tools.tools)  # 描述是给模型看的，不能空
        result = await client.call_tool("browse_questions", {"domain": "rag"})
        assert result.is_error is False
        assert [i["question_id"] for i in result.structured_content["items"]] == ["q_pub1"]


async def test_浏览与单题_私有题与归档题不可见(seeded):
    """只读公共出口：私有题（别人的上传）与归档题**不出现在任何返回里**。

    单题查询对它俩一律「没找到」——不区分原因，私有题 id 不该成为可探测的信号。
    """
    async with Client(server) as client:
        browsed = await client.call_tool("browse_questions", {"page_size": 50})
        ids = {i["question_id"] for i in browsed.structured_content["items"]}
        assert ids == {"q_pub1", "q_pub2"} and browsed.structured_content["total"] == 2

        one = await client.call_tool("get_question", {"question_id": "q_pub1"})
        assert one.structured_content["question"].startswith("q_pub1")
        assert one.structured_content["sources"][0]["source"] == "个人题库"  # 合规四要素随题出
        assert one.structured_content["key_points"] == ["k1", "k2"]  # JSON 字段已解析

        for hidden in ("q_priv", "q_archived", "q_不存在"):
            miss = await client.call_tool("get_question", {"question_id": hidden})
            assert miss.is_error is True
            assert "没有这道题" in miss.content[0].text


async def test_检索_走混合检索并挂来源(seeded, monkeypatch):
    """`search_questions` 委托给生产同款 `hybrid_search`（短查询保留 rerank），结果挂来源。"""
    seen: dict = {}

    async def _fake_search(query, **kwargs):
        seen.update({"query": query, **kwargs})
        return [{"question_id": "q_pub1", "question": "q_pub1 题干", "answer": "q_pub1 答案",
                 "key_points": ["k1"], "follow_ups": [], "domain": "rag", "topic": "测试主题",
                 "difficulty": "L1", "company": None, "round": None, "source": "个人题库"}]

    monkeypatch.setattr(hybrid_search, "hybrid_search", _fake_search)

    async with Client(server) as client:
        result = await client.call_tool("search_questions", {"query": " RAG 怎么评估 ", "limit": 3})

    assert result.is_error is False
    assert seen["query"] == "RAG 怎么评估"  # 两侧空白剥掉再检索
    assert seen["k"] == 3 and seen["filters"] == {"domain": None, "difficulty": None}
    item = result.structured_content["items"][0]
    assert item["question_id"] == "q_pub1" and item["sources"][0]["license"] == "personal"


async def test_检索服务不可用时报出可行动的原因(seeded, monkeypatch):
    """Qdrant / 嵌入服务没起时，模型必须看到**原因与出路**。

    未捕获的异常会被 SDK 换成「Error executing tool X」（原始信息只在服务端日志里）——
    那等于让模型瞎猜；所以工具边界统一转 ToolError（探针实测的 SDK 行为）。
    """

    async def _boom(*args, **kwargs):
        raise ConnectionError("Connection refused: 127.0.0.1:8091")

    monkeypatch.setattr(hybrid_search, "hybrid_search", _boom)

    async with Client(server) as client:
        result = await client.call_tool("search_questions", {"query": "RAG"})

    assert result.is_error is True
    text = result.content[0].text
    assert "检索服务不可用" in text and "browse_questions" in text  # 有原因，也有出路


def test_协议代码只在本包():
    """adapter 隔离（SPEC §7）：`mcp` 只许 `app/mcp_server/` 里 import。

    别的模块一旦直接 import mcp，协议版本升级就会渗进领域代码——隔离是**结构性**的，
    所以这条用源码扫描钉死（比约定可靠）。
    """
    offenders = []
    for path in glob.glob(str(APP_DIR / "**" / "*.py"), recursive=True):
        file = Path(path)
        if file.is_relative_to(APP_DIR / "mcp_server"):
            continue
        if re.search(r"^\s*(import mcp\b|from mcp[\.\s])", file.read_text(encoding="utf-8"), re.M):
            offenders.append(str(file.relative_to(APP_DIR)))

    assert offenders == [], f"这些文件 import 了 mcp（协议代码只许住在 app/mcp_server/）：{offenders}"


async def test_工具描述里带域清单_且来自单一来源(seeded):
    """描述是给模型看的说明书：域取值要列全，且从 `app.domain` 生成（不手写一份会过期的表）。"""
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    description = tools["browse_questions"].description or ""
    assert "agent-architecture" in description and "Agent 认知与架构" in description
    assert "behavioral" in description and "L3" in description
