"""结构化日志（P5-J8）：JSON 行 + request_id / trace_id 上下文。

口径：

- **默认文本**（本地开发 / 测试），`LOG_FORMAT=json` 才切换（compose / HA）；
- `request_id`：沿用上游 `X-Request-Id`（nginx → gateway → business 已生成），没有则生成；
- `trace_id`：从 W3C `traceparent` 头解析（gateway 生成）；没有则生成一个本地 id，
  仅用于日志串联，不代表一次分布式追踪；
- 不打印 token / JWT / key / 题库原文；`user_id` 不主动加（场次级走 Langfuse）。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
instance_var: contextvars.ContextVar[str] = contextvars.ContextVar("instance", default="")


def parse_trace_id(traceparent: str | None) -> str:
    """W3C traceparent → trace-id（32 hex）；格式不对返回空串。"""
    if not traceparent:
        return ""
    parts = traceparent.split("-")
    if len(parts) != 4:
        return ""
    trace_id = parts[1]
    if len(trace_id) != 32 or any(c not in "0123456789abcdef" for c in trace_id):
        return ""
    return trace_id


class JsonFormatter(logging.Formatter):
    """一行一个 JSON；带上下文变量与异常栈。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, var in (
            ("request_id", request_id_var),
            ("trace_id", trace_id_var),
            ("instance", instance_var),
        ):
            value = var.get()
            if value:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure() -> None:
    """按 LOG_FORMAT 切换 JSON；默认不动 logging（测试与本地文本日志保持原样）。"""
    if os.environ.get("LOG_FORMAT", "text").lower() != "json":
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # uvicorn 默认给自己的 logger 装 handler 且 propagate=false；交还给 root。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
    # 访问日志改由 app.main 的中间件记录（同一条日志才能带上 request_id / trace_id）。
    logging.getLogger("uvicorn.access").disabled = True
