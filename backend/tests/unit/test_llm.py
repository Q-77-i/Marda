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


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None
    )


def _usage_chunk() -> SimpleNamespace:
    """结尾 usage 块：choices 为空、只有 usage（include_usage 的产物，不产出正文）。"""
    return SimpleNamespace(
        choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=6)
    )


def _pieces(text: str | None, size: int = 3) -> list[str]:
    if not text:
        return []
    return [text[i:i + size] for i in range(0, len(text), size)]


class FakeStream:
    """模拟 openai AsyncStream：先吐分片，可选中途抛错，最后补 usage 块。"""

    def __init__(self, pieces: list[str], *, error: Exception | None = None) -> None:
        self._pieces = pieces
        self._error = error

    async def __aiter__(self):
        for piece in self._pieces:
            yield _chunk(piece)
        if self._error is not None:
            raise self._error  # 中途断流（已吐过字）
        yield _usage_chunk()


def _status_error(status: int) -> Exception:
    response = httpx.Response(status, request=REQUEST)
    if status == 429:
        return RateLimitError("rate limited", response=response, body=None)
    return APIStatusError("boom", response=response, body=None)


class FakeClient:
    """按序吐出预置结果；Exception 项直接抛出（模拟网络层失败）。

    流式分支（P2-M4）：`stream=True` 时把预置结果包成 FakeStream——
    - FakeStream 原样返回（可编程分片/中途抛错）；
    - `_response(...)`/空 choices 的响应对象按正文自动切片（既有用例零改动）。
    """

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                item = outer._outcomes.pop(0)
                if isinstance(item, Exception):
                    raise item  # 建连期失败（重试覆盖的就是这一段）
                if kwargs.get("stream"):
                    return outer._as_stream(item)
                return item

        self.chat = SimpleNamespace(completions=_Completions())

    @staticmethod
    def _as_stream(item) -> FakeStream:
        if isinstance(item, FakeStream):
            return item
        choices = getattr(item, "choices", None) or []
        content = choices[0].message.content if choices else None
        return FakeStream(_pieces(content))


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
    monkeypatch.setattr(llm._create_stream.retry, "wait", wait_none())

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


async def test_langfuse启用时purpose作为调用名(install, monkeypatch):
    """成本归因（P2-M10）：`purpose` 映射成 drop-in 的 `name`（Langfuse 里按环节归因）。"""
    monkeypatch.setattr(llm.observability, "enabled", lambda: True)
    client = install([_response("好")])
    await llm.chat(MESSAGES, purpose="opening")
    assert client.calls[0]["name"] == "opening"


async def test_langfuse未启用时不传调用名(install):
    """关闭时**不能传**——原生 openai SDK 会把未知参数塞进请求体（DeepSeek 侧 400）。

    （默认关闭 = conftest 的 `_hermetic_langfuse` 夹具。）
    """
    client = install([_response("好")])
    await llm.chat(MESSAGES, purpose="opening")
    assert "name" not in client.calls[0]


async def test_不传purpose时请求不带name(install, monkeypatch):
    monkeypatch.setattr(llm.observability, "enabled", lambda: True)
    client = install([_response("好")])
    await llm.chat(MESSAGES)
    assert "name" not in client.calls[0]


async def test_chat_json同样按purpose命名(install, monkeypatch):
    monkeypatch.setattr(llm.observability, "enabled", lambda: True)
    client = install([_response('{"technical_depth": 3, "comment": "还行"}')])
    await llm.chat_json(MESSAGES, schema=Review, purpose="judge")
    assert client.calls[0]["name"] == "judge"


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


# ── 流式（P2-M4）────────────────────────────────────────────────────────────


async def test_chat_流式分片聚合为全文并剥离两侧空白(install):
    client = install([FakeStream(["  你好", "，我是", "面试官。  "])])

    assert await llm.chat(MESSAGES) == "你好，我是面试官。"
    call = client.calls[0]
    assert call["stream"] is True
    assert call["stream_options"] == {"include_usage": True}  # Langfuse 成本归因的硬前提
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}  # 流式下同样关 thinking


async def test_chat_on_delta逐块回调(install):
    install([FakeStream(["甲", "乙", "丙"])])
    seen: list[str] = []

    text = await llm.chat(MESSAGES, on_delta=seen.append)

    assert seen == ["甲", "乙", "丙"]  # 逐块、保序、不合并
    assert text == "甲乙丙"


async def test_stream_chat_逐块产出(install):
    install([FakeStream(["一", "二", "三"])])

    pieces = [p async for p in llm.stream_chat(MESSAGES)]

    assert pieces == ["一", "二", "三"]  # usage 块不产出正文


async def test_流式_建连失败仍按网络层重试(install):
    client = install([_status_error(429), FakeStream(["重试成功"])])

    assert await llm.chat(MESSAGES) == "重试成功"
    assert len(client.calls) == 2


async def test_流式_首块之后断流不重试(install):
    """已吐字再重试会重复输出——重试只覆盖建连段，中途断流直接抛。"""
    client = install([FakeStream(["半截"], error=APIConnectionError(request=REQUEST))])

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)

    assert excinfo.value.retryable is True  # 可重试的是「用户重发」，不是本层自动重发
    assert len(client.calls) == 1


async def test_流式_空流重请求一次并带纠偏提示(install):
    client = install([FakeStream([]), FakeStream(["补上了"])])

    assert await llm.chat(MESSAGES) == "补上了"
    assert len(client.calls) == 2
    assert "请继续输出" in client.calls[1]["messages"][-1]["content"]


async def test_流式_空流重请求后仍空抛错(install):
    client = install([FakeStream([]), FakeStream([])])

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)

    assert excinfo.value.retryable is False
    assert len(client.calls) == 2


async def test_流式_空流重请求时on_delta不被重复触发(install):
    seen: list[str] = []
    install([FakeStream([]), FakeStream(["补上了"])])

    await llm.chat(MESSAGES, on_delta=seen.append)

    assert seen == ["补上了"]  # 第一次全空、没有块可发，不存在重复


async def test_chat_json_不走流式(install):
    """结构化输出保持非流式（json_object + 整段校验），流式只服务展示类文案。"""
    client = install([_response('{"technical_depth": 4, "comment": "好"}')])

    await llm.chat_json(MESSAGES, schema=Review)

    assert not client.calls[0].get("stream")


# ---------- 上游保护（P2-M9）：闸门 + 熔断在 llm 三条路径上的接线 ----------


def _reliability_env(monkeypatch, **values: str) -> None:
    """改保护参数：settings 是 lru_cache 的，改完必须清缓存 + 重建保护器。"""
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    llm.get_settings.cache_clear()
    llm.reset_reliability()


async def test_上游连续失败触发熔断后快速失败(install, monkeypatch):
    """一次 chat = 建连重试 3 次（一把全 429）→ 记 1 次上游失败；阈值 1 → 开路。"""
    _reliability_env(monkeypatch, LLM_BREAKER_THRESHOLD="1", LLM_BREAKER_COOLDOWN_S="60")
    client = install([_status_error(429)] * 3)

    with pytest.raises(llm.LLMError):
        await llm.chat(MESSAGES)
    assert len(client.calls) == 3  # tenacity 建连重试照旧

    with pytest.raises(llm.LLMError) as excinfo:
        await llm.chat(MESSAGES)
    assert excinfo.value.retryable is True  # 熔断 = 暂时不可用，按可重试处置
    assert len(client.calls) == 3  # 开路期间一个上游请求都没发


async def test_内容类失败不计入熔断(install, monkeypatch):
    """400 这类「我方错」与 JSON 校验失败同族——上游活着时出的错，计入会误开熔断。"""
    _reliability_env(monkeypatch, LLM_BREAKER_THRESHOLD="1")
    client = install([_status_error(400)] * 4)

    for _ in range(4):
        with pytest.raises(llm.LLMError):
            await llm.chat(MESSAGES)

    assert len(client.calls) == 4  # 400 不重试，每次都真发了
    from app.reliability import BreakerState

    assert llm._guard_for(None).breaker.state is BreakerState.CLOSED


async def test_闸门排队超时转可重试错误且不发上游调用(install, monkeypatch):
    _reliability_env(
        monkeypatch, LLM_MAX_CONCURRENCY_FLASH="1", LLM_ACQUIRE_TIMEOUT_S="0.05"
    )
    client = install([_response("不该被调用")])
    guard = llm._guard_for(None)

    async with guard.gate.slot():  # 占满唯一的槽
        with pytest.raises(llm.LLMError) as excinfo:
            await llm.chat(MESSAGES)

    assert excinfo.value.retryable is True
    assert client.calls == []
    assert guard.gate.in_flight == 0  # 自己退出后槽位归零


async def test_chat_json同样过保护器(install, monkeypatch):
    """结构化路径与文案路径共用同一套保护（不是只包了 chat）。"""
    _reliability_env(monkeypatch, LLM_MAX_CONCURRENCY_FLASH="1", LLM_ACQUIRE_TIMEOUT_S="0.05")
    client = install([_response('{"technical_depth": 4, "comment": "好"}')])
    guard = llm._guard_for(None)

    async with guard.gate.slot():
        with pytest.raises(llm.LLMError):
            await llm.chat_json(MESSAGES, schema=Review)

    assert client.calls == []


async def test_流式期间闸门被持有到流结束(install, monkeypatch):
    """流式响应体是长连接：只包住建连段等于没限流——分片到达时必须还占着槽。"""
    _reliability_env(monkeypatch, LLM_MAX_CONCURRENCY_FLASH="1")
    install([_response("你好世界")])
    guard = llm._guard_for(None)
    seen: list[int] = []

    text = await llm.chat(MESSAGES, on_delta=lambda piece: seen.append(guard.gate.in_flight))

    assert seen and all(value == 1 for value in seen)  # 每个分片到达时都占着槽
    assert guard.gate.in_flight == 0  # 流结束后释放
    assert text == "你好世界"


async def test_中途断流计入熔断(install, monkeypatch):
    """半路断流是真的上游故障（与内容类失败不同），必须计数。"""
    _reliability_env(monkeypatch, LLM_BREAKER_THRESHOLD="1")
    install([FakeStream(["半截"], error=_status_error(500))])

    with pytest.raises(llm.LLMError):
        await llm.chat(MESSAGES)

    from app.reliability import BreakerState

    assert llm._guard_for(None).breaker.state is BreakerState.OPEN


async def test_断路器按模型隔离(install, monkeypatch):
    """pro 熔断不影响 flash（报告降级为 flash 的前提）。"""
    _reliability_env(monkeypatch, LLM_BREAKER_THRESHOLD="1")
    client = install([_status_error(429)] * 3 + [_response("flash 正常")])

    with pytest.raises(llm.LLMError):
        await llm.chat(MESSAGES, model="deepseek-v4-pro")
    assert llm._guard_for("deepseek-v4-pro").breaker.state.value == "open"

    # flash 的断路器没被动过，照常出结果
    assert await llm.chat(MESSAGES) == "flash 正常"
