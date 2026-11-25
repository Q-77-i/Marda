"""P5-J8：最小 webhook sink（Alertmanager → 文件）。

只做一件事：把收到的告警 JSON 追加到 `/data/alerts.jsonl`，返回 200。
用于机械验证"告警真的会发"——不解析、不鉴权（仅容器网络）。

运行（compose 已接管）：ALERT_LOG=/data/alerts.jsonl python alert_sink.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

ALERT_LOG = os.environ.get("ALERT_LOG", "/data/alerts.jsonl")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 —— http.server 的接口命名
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001 —— sink 不能因为格式坏了就 500
            payload = {"raw": body.decode("utf-8", "replace")}
        record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with open(ALERT_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt: str, *args) -> None:
        pass  # 静默：sink 自己的访问日志没有价值


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
