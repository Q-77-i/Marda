"""P5-J5 T3：relay 真发布到 Kafka（`-m kafka`，需要真 broker + 真 PG）。

    export MARDA_TEST_DATABASE_URL=postgresql://marda:marda@127.0.0.1:55432/marda_test
    export MARDA_TEST_KAFKA_BOOTSTRAP=127.0.0.1:9094
    uv run pytest -m kafka

只测「发布成功 + 写 published_at」；失败退避由单测（假 publisher）钉住。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.db_postgres import PostgresStore
from app.outbox.events import build_interview_finalized
from app.outbox.relay import KafkaPublisher, run_once

pytestmark = pytest.mark.kafka

DSN = os.environ.get("MARDA_TEST_DATABASE_URL")
BOOTSTRAP = os.environ.get("MARDA_TEST_KAFKA_BOOTSTRAP")

requires = pytest.mark.skipif(
    not (DSN and BOOTSTRAP),
    reason="未配置 MARDA_TEST_DATABASE_URL / MARDA_TEST_KAFKA_BOOTSTRAP",
)


def _event(interview_id: str) -> dict:
    report = {
        "interview_id": interview_id,
        "position": "Agent",
        "interview_type": "tech",
        "scores": {"technical_depth": 3.0},
        "overall": 3.0,
        "domain_scores": {"rag": 2.5},
        "weaknesses": ["rag"],
        "study_advice": [{"domain": "rag", "advice": "补混合检索"}],
    }
    event = build_interview_finalized(
        interview_id=interview_id,
        user_id="u1",
        report=report,
        finished_at=datetime.now(timezone.utc),
    )
    assert event is not None
    return event


def _consume_one(topic: str, timeout_s: float = 15.0) -> dict:
    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"marda-test-{uuid.uuid4().hex}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is None:
                continue
            if message.error():
                raise AssertionError(f"Kafka 消费错误：{message.error()}")
            return {
                "value": json.loads(message.value().decode("utf-8")),
                "headers": {
                    key: value.decode("utf-8")
                    for key, value in (message.headers() or [])
                },
            }
        raise AssertionError(f"{timeout_s}s 内没有收到 {topic} 的消息")
    finally:
        consumer.close()


@requires
def test_relay_把outbox事件发布到kafka并标记已发布():
    store = PostgresStore.connect(DSN, min_size=1, max_size=2)
    topic = f"interview.finalized.test.{uuid.uuid4().hex[:8]}"
    interview_id = f"iv-{uuid.uuid4().hex[:8]}"
    try:
        store.ensure_schema()
        with store._pool.connection() as conn:  # noqa: SLF001
            conn.execute("DELETE FROM agent_outbox")
            conn.commit()

        event = _event(interview_id)
        assert store.enqueue_outbox(event) is True

        publisher = KafkaPublisher(BOOTSTRAP, topic=topic, timeout_s=10.0)
        result = run_once(store, publisher, lease_s=30)

        assert (result.claimed, result.published, result.failed) == (1, 1, 0)
        assert store.count_outbox_pending() == 0

        delivered = _consume_one(topic)
        assert delivered["value"] == event
        assert delivered["headers"]["event_id"] == event["event_id"]
        assert delivered["headers"]["content_type"] == "application/json"
    finally:
        store.close()
