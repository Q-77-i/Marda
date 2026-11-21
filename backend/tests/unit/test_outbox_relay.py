"""P5-J5 T3：outbox relay 的领取 → 发布 → 标记（假 store / 假 publisher 钉语义）。

真 Kafka 的发布在 `tests/integration/test_outbox_kafka.py`（`-m kafka`）。
这里只钉编排：成功才标记、失败只标记失败行、坏载荷不阻塞整批、退避封顶。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.outbox.relay import backoff_seconds, run_once

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, rows):
        self.rows = list(rows)
        self.published: list[str] = []
        self.failed: list[tuple[list[str], str, str]] = []

    def claim_outbox_batch(self, *, limit=100, lease_s=60):
        claimed, self.rows = self.rows[:limit], self.rows[limit:]
        return claimed

    def mark_outbox_published(self, ids):
        self.published.extend(ids)
        return len(ids)

    def mark_outbox_failed(self, ids, *, error, next_attempt_at):
        self.failed.append((list(ids), error, next_attempt_at))
        return len(ids)


class FakePublisher:
    def __init__(self, errors=None, *, raise_exc=None, report_missing=False):
        self.errors = errors or {}
        self.raise_exc = raise_exc
        self.report_missing = report_missing
        self.messages = []

    def publish_batch(self, messages):
        self.messages.extend(messages)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.report_missing:
            return {}
        return {message.id: self.errors.get(message.id) for message in messages}


def _row(row_id: str, *, payload: str | None = None, attempts: int = 0) -> dict:
    event = {
        "event_id": row_id,
        "type": "interview.finalized",
        "payload": {"interview_id": row_id},
    }
    return {
        "id": row_id,
        "partition_key": row_id,
        "payload": payload if payload is not None else json.dumps(event),
        "attempts": attempts,
    }


def test_一轮把整批发布并标记():
    store = FakeStore([_row("e1"), _row("e2")])
    publisher = FakePublisher()

    result = run_once(store, publisher, now=NOW)

    assert (result.claimed, result.published, result.failed) == (2, 2, 0)
    assert store.published == ["e1", "e2"]
    assert store.failed == []
    assert [message.id for message in publisher.messages] == ["e1", "e2"]


def test_发布失败只标记失败行并退避():
    store = FakeStore([_row("e1"), _row("e2")])
    publisher = FakePublisher(errors={"e2": "kafka down"})

    result = run_once(store, publisher, now=NOW, base_s=2.0)

    assert (result.published, result.failed) == (1, 1)
    assert store.published == ["e1"]
    [(ids, error, next_attempt_at)] = store.failed
    assert ids == ["e2"]
    assert error == "kafka down"
    assert datetime.fromisoformat(next_attempt_at) == NOW + timedelta(seconds=2)


def test_空批次不调用发布器():
    store = FakeStore([])
    publisher = FakePublisher()

    result = run_once(store, publisher, now=NOW)

    assert (result.claimed, result.published, result.failed) == (0, 0, 0)
    assert publisher.messages == []


def test_发布器整体异常时整批标记失败():
    store = FakeStore([_row("e1"), _row("e2")])
    publisher = FakePublisher(raise_exc=RuntimeError("broker unreachable"))

    result = run_once(store, publisher, now=NOW)

    assert (result.published, result.failed) == (0, 2)
    assert store.published == []
    assert {tuple(ids)[0] for ids, _, _ in store.failed} == {"e1", "e2"}


def test_坏载荷标记失败但不阻塞同批其它事件():
    store = FakeStore([_row("e1"), _row("bad", payload="not json")])
    publisher = FakePublisher()

    result = run_once(store, publisher, now=NOW)

    assert (result.published, result.failed) == (1, 1)
    assert store.published == ["e1"]
    assert [message.id for message in publisher.messages] == ["e1"]
    assert store.failed[0][0] == ["bad"]
    assert "invalid outbox payload" in store.failed[0][1]


def test_发布器漏报结果按失败处理():
    store = FakeStore([_row("e1")])
    publisher = FakePublisher(report_missing=True)

    result = run_once(store, publisher, now=NOW)

    assert (result.published, result.failed) == (0, 1)
    assert store.failed[0][0] == ["e1"]


def test_指数退避封顶():
    assert backoff_seconds(1, base_s=1.0, max_s=60.0) == 1.0
    assert backoff_seconds(2, base_s=1.0, max_s=60.0) == 2.0
    assert backoff_seconds(3, base_s=1.0, max_s=60.0) == 4.0
    assert backoff_seconds(99, base_s=1.0, max_s=60.0) == 60.0
