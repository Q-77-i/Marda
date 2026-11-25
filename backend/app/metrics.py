"""Prometheus 指标（P5-J8）：进程内埋点，多 worker 用 multiprocess 汇总。

口径：

- **低基数标签**：路由模板 / tier / purpose / outcome；`user_id`、`interview_id`
  不进标签（场次级信息走日志与 Langfuse）。
- **多进程模式由环境变量触发**：默认 compose 是 2 个 uvicorn worker，必须设
  `PROMETHEUS_MULTIPROC_DIR`（counter / histogram 自动落共享目录，Gauge 用
  `multiprocess_mode` 定义聚合语义）；未设置时（测试 / 单进程）直接用默认 REGISTRY。
- 未配置多进程时零额外依赖：只 import prometheus_client。
"""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

HTTP_REQUESTS = Counter(
    "marda_http_requests_total",
    "HTTP requests by route template, method and status",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "marda_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
LLM_SLOT_WAIT = Histogram(
    "marda_llm_slot_wait_seconds",
    "Time spent waiting for a global LLM slot",
    ["tier"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
LLM_CALLS = Counter(
    "marda_llm_calls_total",
    "LLM call outcomes",
    ["tier", "purpose", "outcome"],
)
LLM_BREAKER_OPEN = Gauge(
    "marda_llm_breaker_open",
    "1 when the upstream circuit breaker is open (max across workers)",
    ["tier"],
    multiprocess_mode="max",
)
INTERVIEWS_STARTED = Counter(
    "marda_interviews_started_total",
    "Interviews started",
    ["type"],
)
INTERVIEWS_COMPLETED = Counter(
    "marda_interviews_completed_total",
    "Interviews completed (report persisted)",
    ["type"],
)


def render_latest() -> bytes:
    """渲染当前指标；多进程模式下用 MultiProcessCollector 聚合各 worker。"""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest(REGISTRY)


content_type = CONTENT_TYPE_LATEST


def observe_http(method: str, path: str, status: int, duration_s: float) -> None:
    HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, path=path).observe(duration_s)


def record_llm_slot_wait(tier: str, seconds: float) -> None:
    LLM_SLOT_WAIT.labels(tier=tier).observe(seconds)


def record_llm_call(tier: str, purpose: str | None, outcome: str) -> None:
    LLM_CALLS.labels(tier=tier, purpose=purpose or "unknown", outcome=outcome).inc()


def set_breaker_open(tier: str, is_open: bool) -> None:
    LLM_BREAKER_OPEN.labels(tier=tier).set(1 if is_open else 0)


def record_interview_started(interview_type: str) -> None:
    INTERVIEWS_STARTED.labels(type=interview_type).inc()


def record_interview_completed(interview_type: str) -> None:
    INTERVIEWS_COMPLETED.labels(type=interview_type).inc()
