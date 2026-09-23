"""llm.py 单测：全部离线（fake client 注入），不打真实 API。

网络层重试（429/5xx/超时）无法稳定复现，因此在此覆盖；
真实 happy path（关 thinking 不 400、json_schema 被接受）由 scripts/smoke_llm.py 验。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, RateLimitError
from pydantic import BaseModel
from tenacity import wait_none

from app import llm

REQUEST = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
MESSAGES = [{"role": "user", "content": "你好"}]


class Review(BaseModel):
    """测试用结构化 schema（模拟评分节点输出）。"""

    technical_depth: int
    comment: str


def _response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=6),
    )


def _status_error(status: int) -> Exception:
    response = httpx.Response(status, request=REQUEST)
    if status == 429:
        return RateLimitError("rate limited", response=response, body=None)
    return APIStatusError("boom", response=response, body=None)


class FakeClient:
    """按序吐出预置结果；Exception 项直接抛出（模拟网络层失败）。"""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                item = outer._outcomes.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        self.chat = SimpleNamespace(completions=_Completions())


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """不依赖仓库 .env：注入假密钥，避免无 .env 的机器上 get_settings 抛错。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    llm.get_settings.cache_clear()
    yield
    llm.get_settings.cache_clear()


@pytest.fixture
def install(monkeypatch):
    """注入 fake client，并把退避等待清零（测试不真睡）。"""
    monkeypatch.setattr(llm._create.retry, "wait", wait_none())

    def _install(outcomes: list) -> FakeClient:
        client = FakeClient(outcomes)
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        return client

    return _install


async def test_chat_透传参数并关thinking(install):
    client = install([_response("你好，我是面试官。")])

    text = await llm.chat(MESSAGES, max_tokens=128, temperature=0.5)

    assert text == "你好，我是面试官。"
    call = client.calls[0]
    assert call["model"] == llm.get_settings().deepseek_model
    assert call["messages"] == MESSAGES
    assert call["max_tokens"] == 128
    assert call["temperature"] == 0.5
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}  # 坑位 1
    assert "response_format" not in call


async def test_chat_空内容重请求后成功(install):
    client = install([_response(None), _response("补上了")])

    assert await llm.chat(MESSAGES) == "补上了"
    assert len(client.calls) == 2


async def test_chat_空内容重请求一次后仍空抛错(install):
    client = install([_response(None), _response(None)])

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)

    assert excinfo.value.retryable is False
    assert len(client.calls) == 2


async def test_chat_choices为空也按空内容处理(install):
    client = install(
        [SimpleNamespace(choices=[], usage=None), SimpleNamespace(choices=[], usage=None)]
    )

    with pytest.raises(llm.LLMError):
        await llm.chat(MESSAGES)

    assert len(client.calls) == 2


async def test_chat_json_可指定模型(install):
    """报告节点走深度档（SPEC §3）：model 参数覆盖默认 flash。"""
    client = install([_response('{"technical_depth": 4, "comment": "好"}')])

    await llm.chat_json(MESSAGES, schema=Review, model="deepseek-v4-pro")

    assert client.calls[0]["model"] == "deepseek-v4-pro"


async def test_chat_不指定模型时用默认(install):
    client = install([_response("文案")])

    await llm.chat(MESSAGES)

    assert client.calls[0]["model"] == llm.get_settings().deepseek_model


async def test_chat_json_注入schema提示并用json_object模式(install):
    client = install([_response('{"technical_depth": 4, "comment": "不错"}')])

    result = await llm.chat_json(MESSAGES, schema=Review)

    assert isinstance(result, Review)
    assert result.technical_depth == 4
    call = client.calls[0]
    assert call["response_format"] == {"type": "json_object"}  # 实测 json_schema 不可用
    assert "technical_depth" in call["messages"][0]["content"]  # schema 注入首条 system
    assert MESSAGES == [{"role": "user", "content": "你好"}]  # 入参未被就地修改


async def test_chat_json_有system时追加而非新增轮次(install):
    client = install([_response('{"technical_depth": 5, "comment": "好"}')])
    messages = [{"role": "system", "content": "你是评分官"}, {"role": "user", "content": "评分"}]

    await llm.chat_json(messages, schema=Review)

    sent = client.calls[0]["messages"]
    assert len(sent) == 2  # 不新增对话轮次
    assert sent[0]["content"].startswith("你是评分官")
    assert "technical_depth" in sent[0]["content"]
    assert messages[0]["content"] == "你是评分官"  # 原对象未被就地修改


async def test_chat_json_非法json重请求一次后成功(install):
    client = install([_response("这不是 JSON"), _response('{"technical_depth": 3, "comment": "补"}')])

    result = await llm.chat_json(MESSAGES, schema=Review)

    assert result.technical_depth == 3
    assert len(client.calls) == 2
    # 重请求要带上纠错提示，且原 messages 不被就地修改
    assert len(client.calls[1]["messages"]) == len(client.calls[0]["messages"]) + 1
    assert MESSAGES == [{"role": "user", "content": "你好"}]


async def test_chat_json_两次都非法抛不可重试错误(install):
    client = install([_response("垃圾"), _response("还是垃圾")])

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat_json(MESSAGES, schema=Review)

    assert excinfo.value.retryable is False
    assert len(client.calls) == 2


async def test_chat_json_字段缺失也走重请求(install):
    install([_response('{"technical_depth": 3}'), _response('{"technical_depth": 3, "comment": "补"}')])

    result = await llm.chat_json(MESSAGES, schema=Review)

    assert result.comment == "补"


async def test_429退避后成功(install):
    client = install([_status_error(429), _response("重试成功")])

    assert await llm.chat(MESSAGES) == "重试成功"
    assert len(client.calls) == 2


async def test_持续5xx耗尽后抛可重试错误(install):
    client = install([_status_error(503), _status_error(502), _status_error(500)])

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)

    assert excinfo.value.retryable is True
    assert len(client.calls) == llm.NETWORK_RETRY_ATTEMPTS


async def test_400不重试(install):
    client = install([_status_error(400)])

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)

    assert excinfo.value.retryable is False
    assert len(client.calls) == 1


async def test_连接错误耗尽后抛可重试错误(install):
    client = install([APIConnectionError(request=REQUEST)] * llm.NETWORK_RETRY_ATTEMPTS)

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)

    assert excinfo.value.retryable is True
    assert len(client.calls) == llm.NETWORK_RETRY_ATTEMPTS
