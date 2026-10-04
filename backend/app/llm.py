"""DeepSeek LLM 统一封装（全项目唯一的 LLM 调用入口）。

设计要点（理由见 CLAUDE.md 坑位清单 / SPEC §3）：

- 官方 `openai` SDK + base_url 直连，不经 langchain-deepseek（坑位 2：
  思考模式下 reasoning_content 回传缺陷）；
- **阶段 1 所有调用显式关 thinking**（坑位 1：思考模式与结构化输出冲突会 400）；
- 结构化输出走 `json_object` 模式 + Pydantic 校验（实测 `json_schema` 返回
  400 "This response_format type is unavailable now"，见踩坑记录 T3）；
- 两层重试语义分开：网络层（429/5xx/超时）tenacity 指数退避，
  内容层（空输出 / JSON 校验失败）重请求 1 次；
- 失败统一抛 `LLMError`，`retryable` 供 API 层映射 SSE error 事件；
- **文案类（`chat`）内部走流式**（P2-M4）：逐块回调 `on_delta`，由节点侧透传成 SSE；
  结构化类（`chat_json`）保持非流式（json_object + 整段 Pydantic 校验，无增量语义）；
- **上游保护（P2-M9）**：每次调用都过 `reliability.UpstreamGuard`（按模型分组的
  并发闸门 + 断路器）——闸门持有期 = 整条上游调用的生命周期（流式是整流，不是建连）；
  **只有「上游不可用」类失败计入熔断**（内容类失败按定义就是上游活着时出的错）。
  换模型档位（pro → flash）与换兜底内容**不在这里**——那是调用方（节点）的降级语义。
- **成本归因（P2-M10）**：每次调用可带 `purpose`（出题/评分/报告…），映射成 Langfuse
  generation 的 `name`，云端即可按环节读成本；**只在 Langfuse 启用时传**——原生 openai
  SDK 会把 `name` 当未知参数塞进请求体（DeepSeek 侧 400）。

用法：
    text = await chat([{"role": "user", "content": "出个题"}])
    text = await chat(messages, on_delta=piece => ...)   # 流式：边生成边拿增量
    score = await chat_json(messages, schema=ScoreItem)
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Any, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app import observability, reliability
from app.config import get_settings
from app.reliability import UpstreamBusy

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

NETWORK_RETRY_ATTEMPTS = 3  # 交互场景延迟敏感，比批处理的 4 次少（SPEC §3）
JSON_REASK_ATTEMPTS = 1  # 内容层重请求次数（SPEC §3 "失败重试 1 次"）
REQUEST_TIMEOUT = 60.0
SCHEMA_HINT = """你必须只输出一个 JSON 对象：不要 markdown 代码块、不要任何解释，且符合以下 JSON Schema：
{schema}"""
REASK_HINT = "上次输出不是合法的 JSON。请只输出符合给定 schema 的 JSON 对象，不要任何解释或代码块标记。"


class LLMError(Exception):
    """LLM 调用统一异常。retryable=True 表示可重试（限流/网络），API 层据此提示用户。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _is_retryable(exc: BaseException) -> bool:
    """429 / 5xx / 连接与超时可重试；400/401 等客户端错误重试无意义。"""
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


@lru_cache
def _get_client() -> AsyncOpenAI:
    """单例复用连接池；测试通过 monkeypatch 此函数注入 fake。

    配了 Langfuse key 时换 drop-in 客户端（LLM 调用自动成为 generation，带 usage →
    token 成本可统计，P1-M4）；未配置时用原生 SDK，零额外开销。
    """
    settings = get_settings()
    kwargs = {
        "api_key": settings.deepseek_api_key,
        "base_url": settings.deepseek_base_url,
        "timeout": REQUEST_TIMEOUT,
    }
    if observability.enabled():
        from langfuse.openai import AsyncOpenAI as TracingAsyncOpenAI

        return TracingAsyncOpenAI(**kwargs)
    return AsyncOpenAI(**kwargs)


_guards: dict[str, reliability.UpstreamGuard] = {}


def _guard_for(model: str | None) -> reliability.UpstreamGuard:
    """按模型名取（惰性建）保护器：flash / pro 各一组闸门 + 断路器（P2-M9）。

    进程内单例——多 worker 不共享保护状态（SPEC §11 已写明，demo 单进程形态成立）。
    """
    settings = get_settings()
    name = model or settings.deepseek_model
    guard = _guards.get(name)
    if guard is None:
        pro = name == settings.deepseek_pro_model
        guard = reliability.UpstreamGuard(
            name,
            limit=settings.llm_max_concurrency_pro if pro else settings.llm_max_concurrency_flash,
            acquire_timeout=settings.llm_acquire_timeout_s,
            threshold=settings.llm_breaker_threshold,
            cooldown=settings.llm_breaker_cooldown_s,
            counts_as_failure=_is_retryable,  # 只统计上游类失败，内容类不碰熔断
        )
        _guards[name] = guard
    return guard


def reset_reliability() -> None:
    """清空保护器（测试隔离用：单例状态跨用例会串味）。"""
    _guards.clear()


def _observation_kwargs(purpose: str | None) -> dict[str, Any]:
    """调用用途 → drop-in 的 `name`（成本按环节归因，P2-M10）。

    **只在 Langfuse 启用时给**：drop-in 的 `OpenAiArgsExtractor` 会把 `name` 摘掉，
    而原生 openai SDK 会把它当未知参数塞进请求体（DeepSeek 侧即 400）。
    """
    return {"name": purpose} if purpose and observability.enabled() else {}


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(NETWORK_RETRY_ATTEMPTS),
    wait=wait_exponential_jitter(initial=1, max=10),
    reraise=True,
)
async def _create(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    response_format: dict | None = None,
    model: str | None = None,
    purpose: str | None = None,
) -> Any:
    kwargs: dict[str, Any] = dict(_observation_kwargs(purpose))
    if response_format is not None:
        kwargs["response_format"] = response_format
    return await _get_client().chat.completions.create(
        model=model or get_settings().deepseek_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body={"thinking": {"type": "disabled"}},  # 坑位 1/2：阶段 1 全部关 thinking
        **kwargs,
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(NETWORK_RETRY_ATTEMPTS),
    wait=wait_exponential_jitter(initial=1, max=10),
    reraise=True,
)
async def _create_stream(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    model: str | None = None,
    purpose: str | None = None,
) -> Any:
    """建立流式请求。**重试只覆盖这一步**（建连 + 响应头）——一旦开始吐字，重试就会
    把同一段话说两遍，中途断流交给上层抛错（用户侧「重试」= 重跑失败节点，语义已有）。

    `stream_options.include_usage`：usage 随最后一帧单独回来（choices 为空）。少了它，
    Langfuse 里的流式 generation 没有 token 数、成本读回恒为 0（P1-M4 的验收物）。
    """
    return await _get_client().chat.completions.create(
        model=model or get_settings().deepseek_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body={"thinking": {"type": "disabled"}},  # 坑位 1/2：流式下同样关 thinking
        stream=True,
        stream_options={"include_usage": True},
        **_observation_kwargs(purpose),
    )


async def stream_chat(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    model: str | None = None,
    purpose: str | None = None,
) -> AsyncIterator[str]:
    """流式文案调用：逐块产出正文增量（usage 块与空块不产出）。

    异常语义：建连失败已由 `_create_stream` 重试；**中途断流不重试**，
    统一抛 `LLMError`（`retryable` 反映错误性质，供前端提示）。
    闸门/断路器不通过（`UpstreamBusy`）→ 同抛可重试的 `LLMError`，调用方按降级处置。
    """
    guard = _guard_for(model)
    try:
        # 闸门持有期 = 整条流：流式响应体是长连接，只包住建连段等于没限流（P2-M9）
        async with guard.acquire():
            try:
                response = await _create_stream(
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                    purpose=purpose,
                )
                async for chunk in response:
                    if not chunk.choices:
                        continue  # usage 块（choices 为空是正常形态，不是空响应）
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
            except Exception as exc:
                guard.record_failure(exc)
                raise LLMError(f"LLM 请求失败：{exc}", retryable=_is_retryable(exc)) from exc
            else:
                guard.record_success()
    except UpstreamBusy as exc:
        raise LLMError(f"LLM 暂时不可用：{exc}", retryable=True) from exc


async def _request(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    response_format: dict | None = None,
    model: str | None = None,
    purpose: str | None = None,
) -> str:
    """发一次请求并取正文；网络层异常统一转 LLMError，并过保护器（P2-M9）。"""
    guard = _guard_for(model)
    try:
        response = await guard.run(
            lambda: _create(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                model=model,
                purpose=purpose,
            )
        )
    except UpstreamBusy as exc:
        raise LLMError(f"LLM 暂时不可用：{exc}", retryable=True) from exc
    except Exception as exc:
        raise LLMError(f"LLM 请求失败：{exc}", retryable=_is_retryable(exc)) from exc
    choices = response.choices
    if not choices:
        return ""
    return (choices[0].message.content or "").strip()


async def chat(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    model: str | None = None,
    on_delta: Callable[[str], None] | None = None,
    purpose: str | None = None,
) -> str:
    """文案类调用（开场/出题/追问/结束语）。**内部走流式**，逐块回调 `on_delta`。

    - 单实现纪律：展示类文案只有这一条路径（结构化另有 `chat_json`），流式与非流式
      若各写一套，关 thinking / 空输出重请求 / 错误映射就会各自漂移；
    - 空输出重请求 1 次（与既有语义一致）：**只在一个字都没吐时重试**——已吐字的
      重试会把同一段话说两遍，那种情况按空内容报错；
    - `on_delta` 缺省 None = 不做增量回调（evals / 脚本 / 单测调用不受影响）。

    model 缺省走 flash；深度档（v4-pro）由调用方显式传入（SPEC §3）。
    """
    current = messages
    for attempt in range(2):  # 首次 + 空输出重请求 1 次
        parts: list[str] = []
        async for piece in stream_chat(
            current, max_tokens=max_tokens, temperature=temperature, model=model, purpose=purpose
        ):
            parts.append(piece)
            if on_delta is not None:
                on_delta(piece)
        text = "".join(parts).strip()
        if text:
            return text
        if parts:
            break  # 只吐了空白：重发会重复已发出的块，按空内容处理
        if attempt == 0:
            current = [*messages, {"role": "user", "content": "（请继续输出）"}]
    raise LLMError("LLM 返回空内容", retryable=False)


def _with_schema_hint(messages: list[dict[str, Any]], schema: type[BaseModel]) -> list[dict[str, Any]]:
    """把目标 JSON Schema 注入上下文。

    DeepSeek 的 json_object 模式只保证"输出是 JSON"，字段约束得靠 prompt 说明；
    有 system 消息就追加在其末尾，否则新插一条——不改变原有对话轮次，也不就地改入参。
    """
    hint = SCHEMA_HINT.format(schema=json.dumps(schema.model_json_schema(), ensure_ascii=False))
    if messages and messages[0].get("role") == "system":
        return [{**messages[0], "content": f"{messages[0]['content']}\n\n{hint}"}, *messages[1:]]
    return [{"role": "system", "content": hint}, *messages]


async def chat_json(
    messages: list[dict[str, Any]],
    *,
    schema: type[T],
    max_tokens: int = 2048,
    temperature: float = 0.3,
    model: str | None = None,
    purpose: str | None = None,
) -> T:
    """结构化类调用（评分/提炼/报告）。json_object 模式 + Pydantic 校验，失败重请求 1 次。

    model 缺省走 flash；深度档（v4-pro）由调用方显式传入（SPEC §3）。
    """
    response_format = {"type": "json_object"}
    current = _with_schema_hint(messages, schema)
    last_error = ""
    for attempt in range(JSON_REASK_ATTEMPTS + 1):
        content = await _request(
            current,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            model=model,
            purpose=purpose,
        )
        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            last_error = str(exc)
            logger.warning("chat_json 第 %d 次输出不合法（%s）：%s", attempt + 1, schema.__name__, last_error)
            current = [*current, {"role": "user", "content": REASK_HINT}]
    raise LLMError(f"结构化输出校验失败（重请求 {JSON_REASK_ATTEMPTS} 次后）：{last_error}", retryable=False)
