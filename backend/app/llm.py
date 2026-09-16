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
- 不做限流/熔断/成本统计（阶段 3，SPEC §3）。

用法：
    text = await chat([{"role": "user", "content": "出个题"}])
    score = await chat_json(messages, schema=ScoreItem)
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.config import get_settings

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
    """单例复用连接池；测试通过 monkeypatch 此函数注入 fake。"""
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=REQUEST_TIMEOUT,
    )


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
) -> Any:
    kwargs: dict[str, Any] = {}
    if response_format is not None:
        kwargs["response_format"] = response_format
    return await _get_client().chat.completions.create(
        model=get_settings().deepseek_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body={"thinking": {"type": "disabled"}},  # 坑位 1/2：阶段 1 全部关 thinking
        **kwargs,
    )


async def _request(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    response_format: dict | None = None,
) -> str:
    """发一次请求并取正文；网络层异常统一转 LLMError。"""
    try:
        response = await _create(
            messages, max_tokens=max_tokens, temperature=temperature, response_format=response_format
        )
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
) -> str:
    """文案类调用（开场/出题/追问/结束语）。空输出重请求 1 次，仍空才报错。"""
    content = await _request(messages, max_tokens=max_tokens, temperature=temperature)
    if not content:
        content = await _request(
            [*messages, {"role": "user", "content": "（请继续输出）"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    if not content:
        raise LLMError("LLM 返回空内容", retryable=False)
    return content


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
) -> T:
    """结构化类调用（评分/提炼/报告）。json_object 模式 + Pydantic 校验，失败重请求 1 次。"""
    response_format = {"type": "json_object"}
    current = _with_schema_hint(messages, schema)
    last_error = ""
    for attempt in range(JSON_REASK_ATTEMPTS + 1):
        content = await _request(
            current, max_tokens=max_tokens, temperature=temperature, response_format=response_format
        )
        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            last_error = str(exc)
            logger.warning("chat_json 第 %d 次输出不合法（%s）：%s", attempt + 1, schema.__name__, last_error)
            current = [*current, {"role": "user", "content": REASK_HINT}]
    raise LLMError(f"结构化输出校验失败（重请求 {JSON_REASK_ATTEMPTS} 次后）：{last_error}", retryable=False)
