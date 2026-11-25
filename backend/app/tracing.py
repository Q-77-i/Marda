"""OpenTelemetry 追踪（P5-J8-B）。

口径：

- **默认关闭**：只有 `OTEL_EXPORTER_OTLP_ENDPOINT` 设置且 `OTEL_SDK_DISABLED` 不为 true
  才初始化——本地 / 测试零网络依赖、零额外开销；
- **应用侧全采样**（SDK 默认 parentbased_always_on），由 collector 做 tail sampling
  （错误 100% / 正常 10%）；应用侧不丢错误样本；
- 只做 HTTP（FastAPI）/ httpx / psycopg / redis 的自动化 instrumentation，
  外加 LangGraph **轮次**的手工 span——没有官方 LangGraph instrumentor，
  包内部节点只会产生噪音；
- Langfuse 的 trace_id 由场次派生，与 OTel trace_id **不同源**；在轮次 span 上写
  `langfuse.trace_id` attribute 做显式关联，不宣称同一个 id。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

_configured = False


def enabled() -> bool:
    """Endpoint 配了且没有被显式禁用才启用。"""
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def configure(app) -> bool:
    """给 FastAPI app 装 instrumentation；返回是否真的启用。"""
    global _configured
    if _configured or not enabled():
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    service_name = os.environ.get("OTEL_SERVICE_NAME", "marda-api")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint += "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # **用通用 ASGI middleware，不用 FastAPIInstrumentor**：FastAPI 0.141 / Starlette 1.6
    # 下 FastAPIInstrumentor 静默不产生 HTTP span（实测 Jaeger 只有 psycopg span）；
    # ASGI 协议稳定，OpenTelemetryMiddleware 不依赖框架内部 hook。
    # exclude_spans：默认每个 send/receive 消息一个 INTERNAL span——SSE 的每个 delta、
    # /api/asr 的每个音频帧都会生成，长面试可上万 span；只保留 server span。
    app.add_middleware(
        OpenTelemetryMiddleware,
        excluded_urls="healthz,readyz,metrics",
        exclude_spans=["receive", "send"],
    )
    HTTPXClientInstrumentor().instrument()
    PsycopgInstrumentor().instrument()
    RedisInstrumentor().instrument()
    _configured = True
    return True


def langfuse_trace_id(interview_id: str) -> str:
    """场次 → Langfuse trace_id；未配置 Langfuse 时为空（不建客户端）。"""
    from app import observability

    if not observability.enabled():
        return ""
    try:
        return observability.get_client().create_trace_id(seed=interview_id)
    except Exception:  # noqa: BLE001 —— 关联失败不该影响面试
        return ""


def mark_error(exc: BaseException) -> None:
    """把当前 span 标成 ERROR（供吞掉异常后发 SSE error 的分支调用）。

    `_run` 捕获 LLMError / GraphRecursionError / PersistFailedError 后不再抛出；
    不显式标记的话 collector 的 errors 100% 策略会把失败轮次当成功样本丢掉。
    """
    if not enabled():
        return
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode

    span = otel_trace.get_current_span()
    if span.is_recording():
        span.set_status(Status(StatusCode.ERROR, str(exc)))


@contextmanager
def turn_span(interview_id: str, interview_type: str) -> Iterator[object | None]:
    """一轮问答的 span（未启用时为空操作，调用点不必分支）。"""
    if not enabled():
        yield None
        return
    from opentelemetry import trace as otel_trace

    attributes: dict[str, str] = {
        "interview.id": interview_id,
        "interview.type": interview_type,
    }
    langfuse_id = langfuse_trace_id(interview_id)
    if langfuse_id:
        attributes["langfuse.trace_id"] = langfuse_id
    span = otel_trace.get_tracer("marda").start_span("interview.turn", attributes=attributes)
    with otel_trace.use_span(span, end_on_exit=True):
        yield span
