"""Langfuse 可观测接入（P1-M4）：一次面试 = 一个 trace，token 成本按场次可统计。

成本读回（P2-M10，`scripts/cost_report.py` 与 `scripts/smoke_api.py` 共用）：
`fetch_trace_observations` 从云端拉观测（v2 observations API，等它落全再返回），
`summarize_observations` 是纯函数汇总（按模型 / 按环节 / 按轮次）——单测覆盖它，
真链路读回由 smoke_api 与 cost_report 验。

口径（CLAUDE.md「一次面试 = Langfuse 一个 trace（session_id = 场次）」/ PRD §6）：

- **trace_id 由场次 id 派生**（SDK `create_trace_id(seed)`）——面试是多轮 resume 的多个
  HTTP 请求，派生 id 让它们落进同一个 trace，而不是一场面试散成 N 个 trace；
- `session_id` = 场次、`user_id` = 账号：Langfuse 侧按场次或按人聚合成本；
- LLM 调用经 `langfuse.openai` drop-in（见 `llm._get_client`）自动成为 generation（带 usage）；
- **无 key 时整体降级为零开销**：不 import langfuse、不构造客户端，本地与 CI 无需账号；
- 上报由 SDK 异步批量完成，面试主链路不等待（上报失败的可观测性留阶段 3）。
"""

from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------- 成本读回（P2-M10）

PURPOSE_LABELS = {
    "opening": "开场白",
    "ask": "出题",
    "followup": "追问",
    "judge": "评分",
    "report": "报告",
    "profile": "自我介绍提炼",
    "closing": "收尾",
}
"""LLM 调用用途 → 中文（`llm.*(purpose=...)` 的取值域，成本报告按它分组）。

新增 purpose 忘了登记不会出错：报告按 key 原样打印（宽容，不静默丢）。
"""

_UNNAMED = "(未命名)"
"""没有 `name` 的 generation（接入 P2-M10 之前的历史场次 / 非 LLM 调用库）归这里。"""


def model_of(observation) -> str | None:
    """观测的模型名。v2 API 把它放在 `model` 字段，而 SDK 4.9.1 没声明该字段（落在
    model_extra），别用 `provided_model_name`——那个恒为 None，会误判成「没上报模型名」。"""
    return getattr(observation, "model", None) or (observation.model_extra or {}).get("model")


def _tree_closed(page: list) -> bool:
    """所有 generation 的父节点都在返回集内（观测逐条落库，只判「非空」会拿到半棵树）。"""
    ids = {o.id for o in page}
    return bool(page) and all(
        o.parent_observation_id in ids for o in page if o.type == "GENERATION"
    )


async def fetch_trace_observations(
    trace_id: str, *, extra_complete=None, tries: int = 30, interval: float = 1.0
) -> list:
    """读回云端观测（v2 observations API）；上报是异步批量的，等观测落全再返回。

    齐的判据 = **树闭合**（所有 generation 的父节点都在返回集内）+ 调用方的
    `extra_complete`（可选加强：smoke_api 还要等「报告那次调用」落库——它是全场最后一次
    LLM 调用、落库最晚，是「这一场观测齐了」的天然哨兵）。再**判「条数连续两次不变」收尾**，
    防更晚的观测丢在门外。

    走 v2 而非 `api.trace.get`：**Langfuse 对 2026-09-16 之后新建的组织停用了 legacy
    trace 端点**（410 LEGACY_API_UNAVAILABLE_FOR_NEW_ORGANIZATION），v4 云上读数据只有
    v2 observations / metrics 这一条路。
    """
    def pred(page: list) -> bool:
        return _tree_closed(page) and (extra_complete is None or extra_complete(page))

    client = get_client()  # 调用方要先判 enabled()——未配置时这里会真的建客户端
    last: Exception | None = None
    data: list = []
    stable = 0
    for _ in range(tries):
        try:
            # SDK 的 API 客户端是同步 httpx，扔线程池，别卡事件循环
            resp = await asyncio.to_thread(
                client.api.observations.get_many,
                trace_id=trace_id,
                fields="core,basic,usage,model",
                limit=100,
            )
            page = resp.data
            stable = stable + 1 if pred(page) and len(page) == len(data) else 0
            last = RuntimeError(f"观测尚未落全（当前 {len(page)} 条）")
            data = page
            if stable >= 2:
                return data
        except Exception as exc:  # 未落库时是空/404，其余错误同样重试到超时
            last = exc
        await asyncio.sleep(interval)
    if data:  # 超时但有数据：交给消费侧去报真正的问题，别在这里吞掉
        return data
    raise RuntimeError(f"trace {trace_id} {tries * interval:.0f}s 内未在云端可见：{last}")


def _bucket(target: dict, key: str, cost: float, tokens: int) -> None:
    item = target.setdefault(key, {"calls": 0, "cost": 0.0, "tokens": 0})
    item["calls"] += 1
    item["cost"] += cost
    item["tokens"] += tokens


def _sorted_rows(buckets: dict, labels: dict | None = None) -> list[dict]:
    rows = [
        {"key": key, "label": (labels or {}).get(key, key), **value}
        for key, value in buckets.items()
    ]
    return sorted(rows, key=lambda r: -r["cost"])


def summarize_observations(observations: list) -> dict:
    """观测列表 → 成本视图（纯函数）。

    - **按模型**：flash / v4-pro 各花了多少（降级链一跑，这里立刻看出报告是否走了深度档）；
    - **按环节**：`llm.*(purpose=...)` 映射的 generation name；历史场次没有名字 → `(未命名)`；
    - **按轮次**：挂在第几个 `interview-turn` span 下（按 start_time 排序编号；开场/报告
      这类不在轮次里的归「场外」）——面试官「哪一轮最贵」是能读出来的；
    - `cost_priced=False`：有 generation 但总成本为 0 —— Langfuse 项目没配价格表
      （不是代码问题，报告里要如实说明，否则「成本 0」会被当成结论）。
    """
    generations = [o for o in observations if o.type == "GENERATION"]
    turns = sorted(
        (o for o in observations if o.type != "GENERATION" and o.name == "interview-turn"),
        key=lambda o: o.start_time,
    )
    turn_index = {t.id: i for i, t in enumerate(turns, 1)}

    tokens = {"input": 0, "output": 0, "total": 0}
    cost_total = 0.0
    by_model: dict = {}
    by_purpose: dict = {}
    by_turn: dict = {}
    for gen in generations:
        usage = gen.usage_details or {}
        cost = float((gen.cost_details or {}).get("total", 0.0) or 0.0)
        gen_tokens = int(usage.get("total", 0) or 0)
        tokens["input"] += int(usage.get("input", 0) or 0)
        tokens["output"] += int(usage.get("output", 0) or 0)
        tokens["total"] += gen_tokens
        cost_total += cost
        _bucket(by_model, model_of(gen) or "(未知模型)", cost, gen_tokens)
        _bucket(by_purpose, gen.name or _UNNAMED, cost, gen_tokens)
        slot = turn_index.get(gen.parent_observation_id, 0)  # 0 = 场外（开场/报告/陈词）
        _bucket(by_turn, slot, cost, gen_tokens)

    turn_rows = [
        {"key": key, "label": f"第 {key} 轮" if key else "场外（开场/报告/收尾）", **value}
        for key, value in sorted(by_turn.items())
    ]
    return {
        "generations": len(generations),
        "turn_spans": len(turns),
        "tokens": tokens,
        "cost_total": cost_total,
        "cost_priced": not generations or cost_total > 0,
        "by_model": _sorted_rows(by_model),
        "by_purpose": _sorted_rows(by_purpose, PURPOSE_LABELS),
        "by_turn": turn_rows,
    }
