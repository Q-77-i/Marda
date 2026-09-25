"""Langfuse 可观测接入（P1-M4）：一次面试 = 一个 trace，token 成本按场次可统计。

口径（CLAUDE.md「一次面试 = Langfuse 一个 trace（session_id = 场次）」/ PRD §6）：

- **trace_id 由场次 id 派生**（SDK `create_trace_id(seed)`）——面试是多轮 resume 的多个
  HTTP 请求，派生 id 让它们落进同一个 trace，而不是一场面试散成 N 个 trace；
- `session_id` = 场次、`user_id` = 账号：Langfuse 侧按场次或按人聚合成本；
- LLM 调用经 `langfuse.openai` drop-in（见 `llm._get_client`）自动成为 generation（带 usage）；
- **无 key 时整体降级为零开销**：不 import langfuse、不构造客户端，本地与 CI 无需账号；
- 上报由 SDK 异步批量完成，面试主链路不等待（上报失败的可观测性留阶段 3）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from functools import lru_cache

from app.config import get_settings


def enabled() -> bool:
    """Key 齐才启用（缺一即视为未配置，避免半配置状态下的静默失效）。"""
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


@lru_cache
def get_client():
    """构造 Langfuse 客户端（进程内单例）。

    显式构造（而非依赖环境变量）让 base_url 与 .env 单一来源；该实例同时注册为
    SDK 的全局单例，drop-in openai 复用同一实例——两处若各建各的，span 父子关系会断。
    """
    from langfuse import Langfuse

    settings = get_settings()
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )


@contextlib.contextmanager
def turn_span(interview_id: str, *, user_id: str = "", name: str = "interview-turn") -> Iterator[None]:
    """一轮问答的 trace 上下文；未配置 Langfuse 时为空操作（调用点不必分支）。"""
    if not enabled():
        yield
        return

    from langfuse import propagate_attributes

    client = get_client()
    with propagate_attributes(session_id=interview_id, user_id=user_id or None):
        with client.start_as_current_observation(
            trace_context={"trace_id": client.create_trace_id(seed=interview_id)},
            name=name,
        ):
            yield


def flush() -> None:
    """冲刷缓冲（lifespan close 调用）；未配置时为空操作。"""
    if not enabled():
        return
    get_client().flush()
