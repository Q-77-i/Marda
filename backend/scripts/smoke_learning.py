"""P5-J3 学习计划 smoke：走 nginx → gateway → business 的真实公开链路。

覆盖：注册/登录、报告摘要 → 建计划、列表、今日、PATCH 任务、幂等重放、幂等冲突、
测试提醒。**不走 /internal/v1/**（它不对外暴露；内部端点在 Java 集成测试里覆盖）。

模式：
1. 默认（合成报告）：注册一个临时 smoke 用户，用固定的 rag/memory 短板建计划，
   跑完清理该用户与学习数据（--keep 保留现场）。
2. --interview-id：登录已有账号，读该场报告并映射 report_summary；计划不清理。

前置：
- P5 compose 已起：docker compose -f docker-compose.yml -f docker-compose.p5.yml up -d
- 默认地址 http://localhost:8080；账号密码可用 --username/--password，或自动注册
- 清理需要宿主机连 PG（默认 127.0.0.1:55432，marda/marda；可用
  SMOKE_PG_HOST / SMOKE_PG_PORT / SMOKE_PG_USER / SMOKE_PG_PASSWORD 覆盖）

用法：
  backend/.venv/bin/python backend/scripts/smoke_learning.py
  backend/.venv/bin/python backend/scripts/smoke_learning.py \
      --username your_user --password your_password --interview-id <finished-tech-id>
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

# 摘要派生与服务端同一实现（P5-J5）：脚本不再自己算一份。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outbox.summary import summarize_report  # noqa: E402


def expect(condition: bool, message: str, response: httpx.Response | None = None) -> None:
    if condition:
        return
    detail = ""
    if response is not None:
        detail = f"：HTTP {response.status_code} {response.text[:300]}"
    raise SystemExit(f"❌ {message}{detail}")


def cleanup_smoke_user(user_id: str, username: str) -> None:
    """删掉 smoke 用户及其 Java 学习数据；连不上 PG 时只打印手动清理提示。"""
    try:
        import psycopg
    except ImportError:
        print(f"⚠️ 未安装 psycopg，跳过清理；请手动清理 user_id={user_id}")
        return

    host = os.environ.get("SMOKE_PG_HOST", "127.0.0.1")
    port = int(os.environ.get("SMOKE_PG_PORT", "55432"))
    user = os.environ.get("SMOKE_PG_USER", "marda")
    password = os.environ.get("SMOKE_PG_PASSWORD", "marda")
    databases = (
        ("marda_business", (
            "DELETE FROM learning_task WHERE plan_id IN "
            "(SELECT id FROM learning_plan WHERE user_id = %s)",
            "DELETE FROM learning_plan WHERE user_id = %s",
            "DELETE FROM notification WHERE user_id = %s",
            "DELETE FROM learning_plan_skip WHERE user_id = %s",
        )),
        ("marda", ("DELETE FROM users WHERE id = %s",)),
    )
    for database, statements in databases:
        try:
            with psycopg.connect(
                host=host, port=port, user=user, password=password, dbname=database
            ) as connection:
                with connection.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement, (user_id,))
                connection.commit()
        except Exception as exc:  # noqa: BLE001 — 清理失败不能掩盖 smoke 结果
            print(f"⚠️ 清理 {database} 失败（{exc}）；请手动清理 user_id={user_id}")
    print(f"已清理 smoke 用户 {username} 及其学习数据")


def main() -> None:
    parser = argparse.ArgumentParser(description="P5-J3 学习计划公开链路 smoke")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--interview-id", help="已结束技术面场次；不给则用合成报告")
    parser.add_argument("--keep", action="store_true", help="保留 smoke 用户与计划")
    args = parser.parse_args()

    if bool(args.username) != bool(args.password):
        parser.error("--username 与 --password 必须同时提供")

    client = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0)
    created_user = False
    user_id: str | None = None
    username = args.username

    try:
        if not username:
            username = f"smoke_learning_{uuid.uuid4().hex[:8]}"
            password = "smoke-learning-123"
            response = client.post(
                "/api/auth/register", json={"username": username, "password": password}
            )
            expect(response.status_code == 201, "注册 smoke 用户失败", response)
            created_user = True
        else:
            password = args.password
            response = client.post(
                "/api/auth/login", json={"username": username, "password": password}
            )
            expect(response.status_code == 200, "登录失败", response)

        token = response.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/auth/me", headers=headers)
        expect(me.status_code == 200, "读取当前用户失败", me)
        user_id = me.json()["id"]

        if args.interview_id:
            report_response = client.get(
                f"/api/interviews/{args.interview_id}/report", headers=headers
            )
            expect(report_response.status_code == 200, "读取报告失败", report_response)
            report = report_response.json()["report"]
            expect(
                report.get("interview_type", "tech") != "behavioral",
                "行为面报告不生成学习计划；请换技术面场次",
            )
            source_interview_id = args.interview_id
            position = report.get("position") or "Agent"
            interview_type = report.get("interview_type", "tech")
            report_summary = summarize_report(report)
            expect(
                bool(report_summary and report_summary["weak_domains"]),
                "该报告没有可生成计划的短板域（或 domain_scores 缺分数）",
            )
            goal = None
        else:
            source_interview_id = f"smoke-plan-{uuid.uuid4().hex}"
            position = "Agent"
            interview_type = "tech"
            report_summary = {
                "overall": 3.4,
                "weak_domains": [
                    {"domain": "rag", "score": 2.5, "advice": "补混合检索"},
                    {"domain": "memory", "score": 3.0, "advice": "补压缩策略"},
                ],
            }
            goal = None

        body: dict[str, Any] = {
            "source_interview_id": source_interview_id,
            "position": position,
            "interview_type": interview_type,
            "report_summary": report_summary,
        }
        if goal:
            body["goal"] = goal
        plan_headers = {**headers, "Idempotency-Key": source_interview_id}

        print(f"== 创建学习计划（source={source_interview_id}）==")
        created = client.post("/api/v1/learning/plans", headers=plan_headers, json=body)
        expect(created.status_code in (200, 201), "创建学习计划失败", created)
        plan = created.json()
        plan_id = plan["id"]
        tasks = plan.get("tasks") or []
        print(f"  status={created.status_code} plan_id={plan_id} tasks={len(tasks)}")

        print("== 计划列表 ==")
        listed = client.get(
            "/api/v1/learning/plans?status=active&page=1&page_size=20", headers=headers
        )
        expect(listed.status_code == 200, "读取计划列表失败", listed)
        expect(
            any(item["id"] == plan_id for item in listed.json()["items"]),
            "列表里找不到刚建的计划",
            listed,
        )
        print(f"  total={listed.json()['total']}")

        print("== 今日任务 ==")
        today = client.get("/api/v1/learning/today", headers=headers)
        expect(today.status_code == 200, "读取今日任务失败", today)
        print(f"  date={today.json()['date']} tasks={len(today.json()['tasks'])}")

        target = next(
            (task for task in tasks if task["status"] in ("todo", "in_progress")), None
        )
        if target:
            print("== PATCH 任务 ==")
            next_status = "in_progress" if target["status"] == "todo" else "done"
            patched = client.patch(
                f"/api/v1/learning/tasks/{target['id']}",
                headers=headers,
                json={"status": next_status, "version": target["version"]},
            )
            expect(patched.status_code == 200, "更新任务失败", patched)
            expect(
                patched.json()["version"] == target["version"] + 1,
                "任务版本没有递增",
                patched,
            )
            print(
                f"  task_id={target['id']} status={patched.json()['status']} "
                f"version={patched.json()['version']}"
            )
        else:
            print("== PATCH 任务 == 跳过：没有 todo / in_progress 任务")

        print("== 幂等重放 ==")
        replay = client.post("/api/v1/learning/plans", headers=plan_headers, json=body)
        expect(replay.status_code == 200, "幂等重放没有返回 200", replay)
        expect(replay.json()["id"] == plan_id, "幂等重放返回了不同计划", replay)
        print("  same plan_id, HTTP 200")

        print("== 幂等冲突 ==")
        conflict_body = {**body, "goal": "幂等冲突验证"}
        conflict = client.post(
            "/api/v1/learning/plans", headers=plan_headers, json=conflict_body
        )
        expect(conflict.status_code == 409, "同 key 不同体没有返回 409", conflict)
        expect(
            conflict.json().get("code") == "IDEMPOTENCY_CONFLICT",
            "冲突码不是 IDEMPOTENCY_CONFLICT",
            conflict,
        )
        print("  409 IDEMPOTENCY_CONFLICT")

        print("== 测试提醒 ==")
        reminder = client.post(
            "/api/v1/learning/reminders/test", headers=headers, json={"channel": "in_app"}
        )
        expect(reminder.status_code == 202, "测试提醒失败", reminder)
        print(f"  status={reminder.json()['status']}")

        print("✅ smoke 通过")
    finally:
        client.close()
        if created_user and user_id and not args.keep:
            cleanup_smoke_user(user_id, username or "")
        elif created_user and args.keep:
            print(f"⏸ 保留 smoke 用户 {username}（user_id={user_id}）")
        elif not created_user and user_id:
            print(f"⏸ 使用已有用户 {username}；计划未清理，plan_id 见上")


if __name__ == "__main__":
    sys.exit(main())
