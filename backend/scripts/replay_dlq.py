"""P5-J5 DLQ 重放工具：把 `<topic>.dlq` 的消息重新投回主 topic。

安全约束：
- **不提交消费位点**：消息留在 DLQ，重放可重复执行（消费者靠 processed_event 去重）；
- 默认 `--dry-run`：先看清有多少条、event_id 是什么，再决定是否真发；
- 只做「原样重投」，不修改 payload；修数据请先走人工流程。

用法：
  backend/.venv/bin/python backend/scripts/replay_dlq.py \
      --source interview.finalized.dlq --target interview.finalized --limit 10
  backend/.venv/bin/python backend/scripts/replay_dlq.py \
      --source notification.requested.dlq --target notification.requested --dry-run

`KAFKA_BOOTSTRAP` 可放环境变量（宿主跑 compose 时用 127.0.0.1:9094）。
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid

from confluent_kafka import Consumer, Producer


def main() -> int:
    parser = argparse.ArgumentParser(description="DLQ 重放（不提交位点）")
    parser.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP", ""))
    parser.add_argument("--source", required=True, help="DLQ topic，如 interview.finalized.dlq")
    parser.add_argument("--target", help="目标 topic；缺省 = source 去掉 .dlq")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0, help="最多等多少秒")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.bootstrap:
        parser.error("--bootstrap 或 KAFKA_BOOTSTRAP 必须提供")
    if args.limit < 1:
        parser.error("--limit 必须 >= 1")
    target = args.target or args.source.removesuffix(".dlq")
    if target == args.source:
        parser.error("source 必须以 .dlq 结尾，或显式给 --target")

    consumer = Consumer(
        {
            "bootstrap.servers": args.bootstrap,
            "group.id": f"marda-dlq-replay-{uuid.uuid4().hex}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    producer = Producer(
        {
            "bootstrap.servers": args.bootstrap,
            "acks": "all",
            "enable.idempotence": True,
        }
    )
    consumer.subscribe([args.source])
    replayed = 0
    deadline = time.monotonic() + args.timeout
    try:
        while replayed < args.limit and time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is None:
                continue
            if message.error():
                print(f"⚠️ 消费错误：{message.error()}")
                continue
            value = message.value()
            event_id = None
            event_type = None
            try:
                event = json.loads(value)
                event_id = event.get("event_id")
                event_type = event.get("type")
            except (TypeError, ValueError):
                pass
            print(
                f"{message.topic()} p{message.partition()} o{message.offset()} "
                f"event_id={event_id} type={event_type}"
            )
            if not args.dry_run:
                producer.produce(
                    target,
                    key=message.key(),
                    value=value,
                    headers=message.headers(),
                )
                producer.poll(0)
            replayed += 1
        if not args.dry_run:
            remaining = producer.flush(10)
            if remaining:
                print(f"⚠️ 仍有 {remaining} 条未确认；可重跑（不提交位点，消费者会去重）")
    finally:
        consumer.close()
    print(
        f"replayed={replayed} source={args.source} target={target} "
        f"dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
