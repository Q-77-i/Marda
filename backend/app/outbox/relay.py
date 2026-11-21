"""outbox relay（P5-J5）：独立进程，把 `agent_outbox` 发布到 Kafka。

为什么独立进程（J1 冻结口径）：
- **Kafka 不在面试请求路径**：api 只写本地 outbox；relay 挂了面试照常。
- 单实例多线程 / 多实例都安全：`FOR UPDATE SKIP LOCKED` + 租约（`claim_outbox_batch`）。

语义：**至少一次**。发布成功才写 `published_at`；崩溃后重发由消费者 `processed_event` 去重。
不宣称端到端 exactly-once。

运行：

    DATABASE_URL=postgresql://... KAFKA_BOOTSTRAP=127.0.0.1:9094 \
      python -m app.outbox.relay

只支持 PostgreSQL（SQLite 单机形态不跑 relay；表仍在，语义与 PG 一致）。
"""

from __future__ import annotations

import json
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import REPO_ROOT

logger = logging.getLogger(__name__)

DEFAULT_TOPIC = "interview.finalized"


class RelaySettings(BaseSettings):
    """relay 只需要 DB + Kafka；**不复用 `Settings`**（否则会被 LLM 必填密钥卡住启动）。"""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""
    kafka_bootstrap: str = ""
    kafka_topic: str = DEFAULT_TOPIC
    db_connect_timeout_s: float = 10.0
    outbox_batch_size: int = 100
    outbox_poll_interval_s: float = 1.0
    outbox_lease_s: int = 60
    outbox_backoff_base_s: float = 1.0
    outbox_backoff_max_s: float = 60.0
    outbox_retention_days: int = 30


@dataclass(frozen=True)
class OutboxMessage:
    """parser 产出的可发布事件；id = outbox 行 id = 信封 event_id。"""

    id: str
    key: str
    event: dict[str, Any]


@dataclass
class RelayResult:
    claimed: int = 0
    published: int = 0
    failed: int = 0


class EventPublisher(Protocol):
    def publish_batch(self, messages: list[OutboxMessage]) -> dict[str, str | None]:
        """返回每个 message id 的投递结果：None = 成功，字符串 = 错误。"""
        ...


def backoff_seconds(attempts: int, *, base_s: float, max_s: float) -> float:
    """第 attempts 次失败后的退避（attempts >= 1）；指数增长、封顶。"""
    return min(max_s, base_s * (2 ** max(0, attempts - 1)))


def _parse_row(row: dict) -> tuple[OutboxMessage | None, str | None]:
    try:
        event = json.loads(row["payload"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid outbox payload: {exc}"
    if not isinstance(event, dict) or not event.get("event_id"):
        return None, "invalid outbox payload: missing event_id"
    return (
        OutboxMessage(
            id=str(row["id"]),
            key=str(row.get("partition_key") or ""),
            event=event,
        ),
        None,
    )


def run_once(
    store,
    publisher: EventPublisher,
    *,
    limit: int = 100,
    lease_s: int = 60,
    base_s: float = 1.0,
    max_s: float = 60.0,
    now: datetime | None = None,
) -> RelayResult:
    """领一批 → 发布 → 标记；返回本轮计数（供日志 / 演练证据）。"""
    now = now or datetime.now(timezone.utc)
    rows = store.claim_outbox_batch(limit=limit, lease_s=lease_s)
    result = RelayResult(claimed=len(rows))
    if not rows:
        return result

    errors: dict[str, str] = {}
    messages: list[OutboxMessage] = []
    for row in rows:
        message, error = _parse_row(row)
        if error is not None:
            errors[str(row["id"])] = error
        else:
            messages.append(message)

    if messages:
        try:
            outcomes = publisher.publish_batch(messages)
        except Exception as exc:  # noqa: BLE001 —— 一次 broker 抖动不该让整轮无记录
            outcomes = {message.id: str(exc) for message in messages}
        for message in messages:
            if message.id not in outcomes:
                errors[message.id] = "publisher did not report delivery"
            elif outcomes[message.id]:
                errors[message.id] = str(outcomes[message.id])

    published_ids = [str(row["id"]) for row in rows if str(row["id"]) not in errors]
    if published_ids:
        store.mark_outbox_published(published_ids)

    for row in rows:
        row_id = str(row["id"])
        if row_id not in errors:
            continue
        attempts = int(row.get("attempts") or 0) + 1
        delay = backoff_seconds(attempts, base_s=base_s, max_s=max_s)
        store.mark_outbox_failed(
            [row_id],
            error=errors[row_id],
            next_attempt_at=(now + timedelta(seconds=delay)).isoformat(
                timespec="microseconds"
            ),
        )

    result.published = len(published_ids)
    result.failed = len(errors)
    return result


class KafkaPublisher:
    """librdkafka 生产者：acks=all + 幂等 + zstd；一次 flush 收整批结果。"""

    def __init__(
        self,
        bootstrap: str,
        *,
        topic: str = DEFAULT_TOPIC,
        timeout_s: float = 10.0,
    ) -> None:
        from confluent_kafka import Producer

        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "zstd",
                "linger.ms": 5,
                "message.timeout.ms": int(timeout_s * 1000),
            }
        )
        self._topic = topic
        self._timeout_s = timeout_s

    def publish_batch(self, messages: list[OutboxMessage]) -> dict[str, str | None]:
        outcomes: dict[str, str | None] = {}

        def _callback(message_id: str):
            def _delivery(err, _msg) -> None:
                outcomes[message_id] = None if err is None else str(err)

            return _delivery

        for message in messages:
            try:
                self._producer.produce(
                    self._topic,
                    key=message.key.encode("utf-8") if message.key else None,
                    value=json.dumps(message.event, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "event_id": message.event["event_id"].encode("utf-8"),
                        "content_type": b"application/json",
                    },
                    on_delivery=_callback(message.id),
                )
            except Exception as exc:  # noqa: BLE001 —— 单条入队失败只影响这一条
                outcomes[message.id] = str(exc)
            self._producer.poll(0)

        self._producer.flush(self._timeout_s)
        for message in messages:
            if message.id not in outcomes:
                outcomes[message.id] = "delivery timeout"
        return outcomes


def _install_stop_handlers() -> Callable[[], bool]:
    state = {"stop": False}

    def _stop(_signum, _frame) -> None:
        state["stop"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return lambda: state["stop"]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = RelaySettings()
    if not settings.database_url:
        logger.error("DATABASE_URL 未配置；relay 只支持 PostgreSQL")
        return 2
    if not settings.kafka_bootstrap:
        logger.error("KAFKA_BOOTSTRAP 未配置")
        return 2

    from app.db_postgres import get_store

    # 三个池参数都要显式给：`get_store` 只要有一个是 None 就会去实例化 `Settings`，
    # 而 relay 镜像没有（也不该有）LLM 密钥。
    store = get_store(
        settings.database_url,
        min_size=1,
        max_size=2,
        timeout=settings.db_connect_timeout_s,
    )
    # relay 可能比 api 先起：自己补一次 schema（幂等），否则第一轮会因表不存在而退避。
    store.ensure_schema()
    publisher = KafkaPublisher(settings.kafka_bootstrap, topic=settings.kafka_topic)
    stop = _install_stop_handlers()
    next_cleanup = 0.0

    logger.info(
        "outbox relay started topic=%s batch=%d lease=%ds",
        settings.kafka_topic,
        settings.outbox_batch_size,
        settings.outbox_lease_s,
    )
    try:
        while not stop():
            try:
                result = run_once(
                    store,
                    publisher,
                    limit=settings.outbox_batch_size,
                    lease_s=settings.outbox_lease_s,
                    base_s=settings.outbox_backoff_base_s,
                    max_s=settings.outbox_backoff_max_s,
                )
                if result.claimed:
                    logger.info(
                        "relay claimed=%d published=%d failed=%d pending=%d",
                        result.claimed,
                        result.published,
                        result.failed,
                        store.count_outbox_pending(),
                    )
            except Exception as exc:  # noqa: BLE001 —— relay 不退出，退避后重试
                logger.exception("relay 轮询失败：%s", exc)
                time.sleep(settings.outbox_backoff_max_s)
                continue

            if time.monotonic() >= next_cleanup:
                removed = store.cleanup_outbox(
                    older_than_days=settings.outbox_retention_days
                )
                if removed:
                    logger.info("outbox 清理 published 行：%d", removed)
                next_cleanup = time.monotonic() + 86400

            if result.claimed == 0:
                time.sleep(settings.outbox_poll_interval_s)
    finally:
        store.close()
        logger.info("outbox relay stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
