"""评分官用图监控（P2-M6，2026-10-04 用户补充口径）——手动跑，不进 pytest。

**要回答的问题**：评分官看图是有用的，还是白付 token？用数据决定、不用「应该看」决定。

口径：
- 样本 = checkpoints 里「题记录带 image_ids」的场次（真库；题级粒度）；
- 判定 = 对每条带图题，把（题目 + 候选人**文字**回答 + judge 输出全文 + 截图）交给
  flash 视觉，问「judge 输出是否引用了只能在截图中看到的信息（文字回答里没有）」→ 是/否；
- 输出 = 逐场比率 + **连续零引用场次数** + 明细（每题一行，人工可复核）。

**降级读法（约定，拍板权在用户）**：连续 N=3 场带图场次「judge 输出引用率 = 0」
→ 说明图对评分是无效 token，届时把决策 ① 降级为「评分官不看图」（改 `judge_messages`
的 image_parts 传参处即可，一处开关）。

用法（**推荐在容器里跑**——checkpoints 是 WAL，api 容器开着时从宿主机直读会
「database disk image is malformed」，这是 P1-M5 记过的跨 macOS 绑定挂载坑）：

  docker compose exec -T api /app/.venv/bin/python scripts/eval_judge_vision.py

容器没起时可宿主机直跑：cd backend && uv run python scripts/eval_judge_vision.py
（要审计非正式库：先导出 DB_PATH / CHECKPOINT_DB_PATH / UPLOAD_DIR 再跑——env 优先于 .env）
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

# 审计调用**不上报 Langfuse**（这是运维巡检，不是面试链路）——先于 app.config 首次读取
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.graph.graph import make_serde  # noqa: E402
from app.tools import images as image_store  # noqa: E402

AUDIT_PROMPT = (
    "你是面试系统的质量审计员。下面给你：一道面试题、候选人的**文字**回答、"
    "评分官对该回答的评分输出，以及候选人随回答上传的截图。\n"
    "请只回答一个字：评分官的输出里，是否有内容**只能从截图里看到**（文字回答里没有）？\n"
    "「是」= 评分官显然读了图（引用了图里的代码细节/组件名/结构等）；\n"
    "「否」= 评分官输出只用文字回答就能得出。"
)


def sessions_with_images() -> list[dict]:
    """扫 checkpoints：线程 → 带图的题记录（题目/回答/judge 输出/image_ids）。"""
    serde = make_serde()
    settings = get_settings()
    started_at: dict[str, str] = {}
    with sqlite3.connect(settings.db_path) as conn:
        for interview_id, started in conn.execute("SELECT id, started_at FROM interviews"):
            started_at[interview_id] = started or ""

    out: list[dict] = []
    skipped: list[str] = []
    try:
        conn_ctx = sqlite3.connect(settings.checkpoint_db_path)
    except sqlite3.DatabaseError as exc:
        _die_unreadable(settings.checkpoint_db_path, exc)
        return []
    with conn_ctx as conn:
        try:
            threads = [r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]
        except sqlite3.DatabaseError as exc:
            _die_unreadable(settings.checkpoint_db_path, exc)
            return []
        for thread in threads:
            try:
                row = conn.execute(
                    "SELECT type, checkpoint FROM checkpoints WHERE thread_id = ? "
                    "ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread,),
                ).fetchone()
                cp = serde.loads_typed((row[0], row[1])) if row is not None else None
            except Exception:
                # 读不出的线程（含历史损坏页）跳过并计数——审计不因个例中断，也不静默
                skipped.append(thread)
                continue
            if cp is None:
                continue
            questions = (cp.get("channel_values") or {}).get("answered_questions") or []
            items = []
            for q in questions:
                image_ids = list(getattr(q, "image_ids", None) or [])
                if not image_ids:
                    continue
                score = getattr(q, "score", None)
                items.append({
                    "question": getattr(q, "text", ""),
                    "answer": getattr(q, "answer", "") or "",
                    "judge_output": (
                        f"点评：{score.comment}\n覆盖：{'；'.join(score.covered_key_points)}"
                        f"\n漏点：{'；'.join(score.missed_key_points)}"
                        if score
                        else "（无评分输出）"
                    ),
                    "image_ids": image_ids,
                })
            if items:
                out.append({
                    "interview_id": thread,
                    "started_at": started_at.get(thread, ""),
                    "items": items,
                })
    out.sort(key=lambda s: s["started_at"])
    if skipped:
        print(f"⚠️ {len(skipped)} 个线程读不出已跳过（历史损坏页，多为无主孤儿线程）："
              f"{[t[:8] for t in skipped]}")
    return out


def _die_unreadable(path: Path, exc: Exception) -> None:
    """WAL 跨绑定挂载直读失败：给可执行的下一步，不甩堆栈。"""
    print(f"!! 读不了 checkpoints（{path}）：{exc}\n"
          f"   若 api 容器正在运行，改在容器内跑：\n"
          f"     docker compose exec -T api /app/.venv/bin/python scripts/eval_judge_vision.py\n"
          f"   （checkpoints 是 WAL，跨 macOS 绑定挂载宿主机直读会 malformed，P1-M5 记过）")
    sys.exit(2)


async def judge_uses_image(session: dict, item: dict) -> bool:
    """单条判定：judge 输出是否引用了图内独有信息。"""
    parts = image_store.load_image_parts(
        get_settings().upload_dir, session["interview_id"], item["image_ids"]
    )
    if not parts:
        print(f"    （图文件缺失，跳过该题：{session['interview_id'][:8]}）")
        return False
    messages = [
        {"role": "system", "content": AUDIT_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": (
                f"【题目】{item['question']}\n【候选人文字回答】\n{item['answer']}\n"
                f"【评分官输出】\n{item['judge_output']}"
            )},
            *parts,
        ]},
    ]
    reply = (await llm.chat(messages, max_tokens=8, temperature=0)).strip()
    return reply.startswith("是")


async def main() -> None:
    sessions = sessions_with_images()
    if not sessions:
        print("真库里没有「带图场次」——功能刚上线属正常；带图面试产生后重跑本脚本。")
        return

    print(f"带图场次 {len(sessions)} 场（按开始时间排序）\n")
    session_ratios: list[tuple[str, float, int]] = []
    for session in sessions:
        hits = 0
        for item in session["items"]:
            used = await judge_uses_image(session, item)
            hits += int(used)
            print(f"  [{'是' if used else '否'}] 场次 {session['interview_id'][:8]} · "
                  f"{item['question'][:40]}…（图 {len(item['image_ids'])} 张）")
        ratio = hits / len(session["items"])
        session_ratios.append((session["interview_id"], ratio, len(session["items"])))
        print(f"  → 本场引用率 {hits}/{len(session['items'])} = {ratio:.0%}\n")

    zero_streak = 0
    for _sid, ratio, _n in reversed(session_ratios):  # 从最近往回数
        if ratio == 0:
            zero_streak += 1
        else:
            break
    total_hits = sum(round(r * n) for _sid, r, n in session_ratios)
    total_items = sum(n for _sid, _r, n in session_ratios)
    print(f"汇总：{total_hits}/{total_items} 条 judge 输出引用了图内信息（{total_hits / total_items:.0%}）")
    print(f"连续零引用场次：{zero_streak}（达到 3 场 → 按约定建议把决策 ① 降级为「评分官不看图」，由人拍板）")


if __name__ == "__main__":
    asyncio.run(main())
