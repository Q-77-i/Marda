"""T5 smoke：真实 DeepSeek + Qdrant，走 HTTP API（本地 uvicorn）跑一场短面试。

用法：cd backend && uv run python scripts/smoke_api.py
      SMOKE_QUESTION_COUNT=10 uv run python scripts/smoke_api.py   # 长场次：看同域成块与难度曲线

流程：起 uvicorn 子进程（8765 端口）→ healthz 就绪 → 注册账号（FR-23）→ 反向验证
未登录 401 → 题库（P1-M6 FR-12/FR-14：分面 / 浏览分页 / 关键词检索 / 容量校验，
与直查 SQL 对账）+ 私有题库（P1-M7 FR-13：上传判重 / 隔离 / 混入出题 / 管理闭环 /
计入容量）+ 难度锁定场次 → POST 创建（SSE 开场）→ 循环 POST 消息到 done →
GET 报告 + 决策回放（FR-21）+ 会话恢复 + 历史列表 → 核对场次归属，验证落库与用户隔离。

P1-M4.7-D 人味层（真实链路上才看得出效果，故放在 smoke 而非单测）：
重连问候（?reconnect=true 重发当前题干）、六类衔接语的连读观感、结束陈词是否踩红线；
技术题同域成块 → 打印每块的难度曲线（观测点：会不会一段卡在高难度）。
依赖：.env（DEEPSEEK_API_KEY / JWT_SECRET）；Qdrant 容器可选——检索不可用时出题走 LLM 生成降级。
Langfuse（P1-M4）：.env 配了 LANGFUSE_* 时把该场次的 trace 从云端读回来核对
（session_id / 轮次 span / generation 归父），未配置则跳过——这也是 FR-21 的验收口径。
隔离（T7a-R1）：业务库与 checkpointer 落 /tmp 临时文件，验证不污染正式数据
（题库 Qdrant 只读，不受影响）。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须先于 app.config 的 get_settings 首次调用注入临时库路径（lru_cache）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marda-smoke-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "marda.sqlite3")
os.environ["CHECKPOINT_DB_PATH"] = str(_TMP_DIR / "checkpoints.sqlite3")

# 出题检索走 SQLite join questions 表 → 从正式库拷贝只读快照到临时库
import sqlite3

_REAL_DB = Path(__file__).resolve().parents[2] / "data" / "marda.sqlite3"
with sqlite3.connect(os.environ["DB_PATH"]) as _dst:
    _dst.execute("ATTACH DATABASE ? AS real", (str(_REAL_DB),))
    _dst.execute("CREATE TABLE questions AS SELECT * FROM real.questions")
    # 题库浏览要挂来源明细（M5 拆表），来源表同样拷快照
    _dst.execute("CREATE TABLE question_sources AS SELECT * FROM real.question_sources")
    _dst.execute("DETACH DATABASE real")

import httpx

from app import db, observability
from app.config import get_settings
from app.domain import DOMAIN_LABELS, project_count
from app.graph.rules.transition import domain_label
from app.tools import question_search

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

# 题量（轮次语义）：默认 2 轮快跑；SMOKE_QUESTION_COUNT=10 可跑长场次验证同域成块与难度曲线
QUESTION_COUNT = int(os.environ.get("SMOKE_QUESTION_COUNT", "2"))

# 账号（FR-23）：临时库每次全新，用时间戳保证用户名不撞（规则 [A-Za-z0-9_]，3-32）
SMOKE_USER = f"smoke_{int(time.time())}"
SMOKE_PASSWORD = "smoke-secret-123"

# 预置候选人回答（smoke 只验证链路，不验证回答质量；数量不足时循环喂通用作答）
CANDIDATE_ANSWERS = [
    "你好，我是应届生，主要做 Agent 和 RAG 方向。项目里用 LangGraph 搭过一个面试机器人，"
    "做过向量检索和工具调用，最近在看多智能体协作。",
    "我先说整体思路：这个问题我会从数据流和状态管理两个角度拆……（模拟作答）",
    "第二题我想想……核心是把检索和生成解耦，再加一层重排……（模拟作答）",
    "场景题我的方案是：入口做意图路由，主流程用状态机管编排，工具侧统一走适配层……（模拟作答）",
    "请问贵团队在 Agent 方向的技术栈和分工是怎样的？",
    "谢谢，最后问一个：应届生入职后一般怎么成长？",
]
FALLBACK_ANSWER = "好的，我再补充一点：整体上我会优先保证流程能跑通，再逐步加监控和降级……（模拟作答）"


async def _events(response) -> list[dict]:
    """解析 SSE 流为 (event, data) 列表。"""
    out, current = [], {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append((current["event"], current.get("data")))
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    return out


async def wait_ready() -> None:
    for _ in range(60):
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                if (await client.get(f"{BASE}/healthz")).status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("uvicorn 未在 30s 内就绪")


def _model_of(obs) -> str | None:
    """观测的模型名。v2 API 把它放在 `model` 字段，而 SDK 4.9.1 没声明该字段（落在
    model_extra），别用 `provided_model_name`——那个恒为 None，会误判成「没上报模型名」。"""
    return getattr(obs, "model", None) or (obs.model_extra or {}).get("model")


async def _fetch_observations(langfuse_client, trace_id: str, *, report_model: str) -> list:
    """读回云端观测（v2 observations API）；上报是异步批量的，等观测落全再返回。

    判据三条，缺一都会拿到**偏小的快照**（断言本身不会错，但打印的轮次数/token/成本会少算，
    实测漏过 2 个 span + 2 个 generation + 整条报告调用）：

    1. **树闭合**——所有 generation 的父节点都在返回集内。观测逐条落库，只判「非空」会拿到
       半棵树（generation 已到、它挂的轮次 span 还没到），此时的父子断言必然误报。
    2. **报告调用已到**——报告是全场最后一次 LLM 调用，落库最晚，是「这一场观测齐了」的
       天然哨兵；顺带把 SPEC §3「报告走深度档」变成断言。
    3. **连续两次条数不变**——收尾确认没有更晚落下的观测。

    走 v2 而非 `api.trace.get`：**Langfuse 对 2026-09-16 之后新建的组织停用了 legacy
    trace 端点**（410 LEGACY_API_UNAVAILABLE_FOR_NEW_ORGANIZATION），v4 云上读数据只有
    v2 observations / metrics 这一条路。
    """
    last: Exception | None = None
    data: list = []
    stable = 0
    for _ in range(30):
        try:
            # SDK 的 API 客户端是同步 httpx，扔线程池，别卡事件循环
            resp = await asyncio.to_thread(
                langfuse_client.api.observations.get_many,
                trace_id=trace_id, fields="core,basic,usage,model", limit=100,
            )
            page = resp.data
            ids = {o.id for o in page}
            gens = [o for o in page if o.type == "GENERATION"]
            done = (
                bool(page)
                and all(o.parent_observation_id in ids for o in gens)
                and any(_model_of(o) == report_model for o in gens)
            )
            stable = stable + 1 if done and len(page) == len(data) else 0
            last = RuntimeError(f"观测尚未落全（当前 {len(page)} 条）")
            data = page
            if stable >= 2:
                return data
        except Exception as exc:  # 未落库时是空/404，其余错误同样重试到超时
            last = exc
        await asyncio.sleep(1)
    if data:  # 超时但有数据：交给断言去报真正的问题，别在这里吞掉
        return data
    raise RuntimeError(f"trace {trace_id} 30s 内未在云端可见：{last}")


async def main() -> None:
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        await wait_ready()
        print("=" * 60)
        print(f"T5 smoke：真实 DeepSeek + Qdrant，HTTP API 跑 {QUESTION_COUNT} 轮面试")
        print("=" * 60)

        async with httpx.AsyncClient(timeout=120) as client:
            # 账号（FR-23）：注册即登录，后续请求全部带 Bearer
            r = await client.post(
                f"{BASE}/api/auth/register",
                json={"username": SMOKE_USER, "password": SMOKE_PASSWORD},
            )
            assert r.status_code == 201, f"注册失败: {r.status_code} {r.text}"
            token = r.json()["token"]
            client.headers["Authorization"] = f"Bearer {token}"
            me = (await client.get(f"{BASE}/api/auth/me")).json()
            print(f"账号 OK：{SMOKE_USER}（id={me['id'][:8]}…）")
            # 反向验证：未登录必须被拦
            async with httpx.AsyncClient(timeout=30) as anon:
                for path in ("/api/interviews", "/api/auth/me", "/api/bank/questions",
                             "/api/bank/facets", "/api/bank/capacity",
                             "/api/bank/private/questions"):
                    assert (await anon.get(f"{BASE}{path}")).status_code == 401, f"{path} 未拦截"
                assert (await anon.post(
                    f"{BASE}/api/interviews", json={"position": "x", "question_count": 2}
                )).status_code == 401
            print("未登录 401 OK（面试列表 / me / 创建 / 题库三端点 / 私有题库）")

            # ── 题库（P1-M6 FR-12）：分面 / 浏览分页 / 关键词检索 ──────────────
            # 与直查 SQL 对账：接口写错了这里立刻暴露（题库只读，对账无副作用）
            facets = (await client.get(f"{BASE}/api/bank/facets")).json()
            with sqlite3.connect(get_settings().db_path) as conn:
                enabled = conn.execute(
                    "SELECT COUNT(*) FROM questions WHERE status='enabled'"
                ).fetchone()[0]
            assert set(facets) == {"domain", "difficulty", "company", "round"}, facets.keys()
            assert sum(c["count"] for c in facets["domain"]) == enabled, "分面计数与题库总数不符"
            # 难度按档位排（有序维度，计数序会把 L2 顶到 L1 前面）；其余三维计数降序
            assert [c["value"] for c in facets["difficulty"]] == ["L1", "L2", "L3"], \
                f"难度分面未按档位排：{[c['value'] for c in facets['difficulty']]}"
            for column, values in facets.items():
                if column == "difficulty":
                    continue
                counts = [c["count"] for c in values]
                assert counts == sorted(counts, reverse=True), f"{column} 计数未按降序"
            print(f"分面 OK：{len(facets['domain'])} 域 / {len(facets['company'])} 厂商 / "
                  f"{len(facets['round'])} 面次（enabled {enabled} 题）")

            params = {"domain": "rag", "difficulty": "L2", "page": 1, "page_size": 5}
            browse = (await client.get(f"{BASE}/api/bank/questions", params=params)).json()
            with sqlite3.connect(get_settings().db_path) as conn:
                expected = conn.execute(
                    "SELECT COUNT(*) FROM questions WHERE status='enabled'"
                    " AND domain='rag' AND difficulty='L2'"
                ).fetchone()[0]
            assert browse["mode"] == "browse" and browse["total"] == expected, \
                f"浏览总数 {browse['total']} ≠ 直查 {expected}"
            assert all(i["domain"] == "rag" and i["difficulty"] == "L2" for i in browse["items"])
            no_url = 0
            for item in browse["items"]:
                names = [s["source"] for s in item["sources"]]
                assert item["source"] in names, f"{item['question_id']} 缺主源明细"
                assert names[0] == item["source"], f"{item['question_id']} 主源未排首位：{names}"
                assert all(s["license"] for s in item["sources"]), "来源缺 license（合规红线）"
                no_url += sum(1 for s in item["sources"] if not s["url"])
            second = (await client.get(
                f"{BASE}/api/bank/questions", params={**params, "page": 2}
            )).json()
            page1 = {i["question_id"] for i in browse["items"]}
            page2 = {i["question_id"] for i in second["items"]}
            assert not (page1 & page2), f"分页重复（排序不稳定）：{page1 & page2}"
            assert len(page1 | page2) == min(expected, 10), "两页合计条数对不上"
            print(f"浏览 OK：rag/L2 共 {expected} 题，前 5 条主源排首位、license 齐"
                  f"（其中 {no_url} 条无 url = 个人题库本地语料）；第 1/2 页无重叠")

            search = (await client.get(
                f"{BASE}/api/bank/questions", params={"q": "RAG 切片策略怎么选"}
            )).json()
            assert search["mode"] == "search" and search["total"] is None
            assert search["items"], "关键词检索无结果（Qdrant/重排链路未就绪？）"
            print(f"关键词检索 OK：混合检索 top {len(search['items'])} 条，"
                  f"首条「{search['items'][0]['question'][:28]}…」"
                  f"[{search['items'][0]['domain']}/{search['items'][0]['difficulty']}]")

            # ── 容量校验（P1-M6 FR-14）：难度 × 题数的直供能力 ──────────────
            capacity = (await client.get(
                f"{BASE}/api/bank/capacity", params={"counts": "5,10,15"}
            )).json()["options"]
            assert len(capacity) == 12, f"4 难度 × 3 题数 应 12 项：{len(capacity)}"
            verdict = {(o["difficulty"], o["question_count"]): o for o in capacity}
            for option in capacity:
                assert option["ok"] is (not option["shortfalls"]), "ok 与不足明细不一致"
                assert option["base"] == ("L1" if option["difficulty"] == "adaptive"
                                          else option["difficulty"])
                if option["ok"]:
                    continue
                with sqlite3.connect(get_settings().db_path) as conn:  # 不足明细逐条复核
                    for s in option["shortfalls"]:
                        have = conn.execute(
                            "SELECT COUNT(*) FROM questions WHERE status='enabled'"
                            " AND difficulty=? AND domain=?", (option["base"], s["domain"])
                        ).fetchone()[0]
                        assert s["available"] == have, \
                            f"{s['domain']} 供给 {s['available']} ≠ 直查 {have}"
                        assert have < s["required"], f"{s['domain']} 并未短缺（直查 {have}）"
            for count in (5, 10, 15):  # 自适应 = 从 L1 起步，直供能力应与 L1 同款
                assert verdict[("adaptive", count)]["ok"] == verdict[("L1", count)]["ok"]
            blocked = [o for o in capacity if not o["ok"]]
            print(f"容量校验 OK：12 项中 {len(blocked)} 项直供不足 —— " + (
                "；".join(
                    f"{o['difficulty']} × {o['question_count']} 题（"
                    + "、".join(f"{domain_label(s['domain'])} 需 {s['required']} 有 {s['available']}"
                                for s in o["shortfalls"]) + "）"
                    for o in blocked
                ) if blocked else "真实题库每档都能直供"))
            if not blocked:  # 数据变化不该让 smoke 变红，但要知道禁用态在真库上没被验到
                print("  ⚠ 真库当前无直供不足组合——禁用态（FR-14 前端）的真数据验收要靠 M9 难度补样")

            # ── 私有题库（P1-M7 FR-13）：上传 → 管理 → 隔离 → 混入出题 ──────
            # 全程只写临时库副本（questions 表启动时已拷快照），正式库零写入
            private_doc = (
                "【题目】SMOKE 私有题一：为什么哈希表查找是 O(1)？\n"
                "【答案】按 key 直接算出桶位置，代价是空间与冲突处理。\n"
                "【关键点】散列定位；冲突处理\n"
                "【追问】开放寻址与链地址怎么选？\n\n"
                "【题目】SMOKE 私有题二：什么场景该避免哈希表？\n"
                "【答案】需要有序遍历或范围查询时，哈希给不出顺序。\n"
            )
            private_file = {"file": ("我的笔记.md", private_doc.encode(), "text/markdown")}
            up = await client.post(f"{BASE}/api/bank/private/upload", files=private_file,
                                   data={"domain": "algorithms", "difficulty": "L1"})
            assert up.status_code == 200, f"私有题上传失败：{up.status_code} {up.text}"
            upload = up.json()
            assert (upload["imported"], upload["errors"]) == (2, []), upload
            # 重传同一份：按「同用户 + 同题干」判重，跳过且不覆盖（用户可能已改过答案）
            again = (await client.post(f"{BASE}/api/bank/private/upload", files=private_file,
                                       data={"domain": "algorithms", "difficulty": "L1"})).json()
            assert again["imported"] == 0 and len(again["duplicated"]) == 2, f"重复题未跳过：{again}"

            listed = (await client.get(f"{BASE}/api/bank/private/questions")).json()
            assert listed["total"] == 2, listed
            private_ids = {i["question_id"] for i in listed["items"]}
            assert all(qid.startswith("p_") for qid in private_ids), private_ids
            assert all(i["domain"] == "algorithms" and i["difficulty"] == "L1"
                       for i in listed["items"]), "表单默认值未落到私有题上"
            assert listed["items"][0]["sources"][0]["source_detail"] == "我的笔记.md", "来源没记文件名"
            print(f"私有题上传 OK：导入 2 / 重传判重 2，id 前缀 p_，来源记文件名"
                  f"（{sorted(private_ids)[0]}…）")

            # 隔离：另一个账号四处不可见（列表 / 按 id 直取 / 公共浏览 / 公共检索）
            bob = httpx.AsyncClient(timeout=30)
            rb = await bob.post(f"{BASE}/api/auth/register",
                                json={"username": f"{SMOKE_USER}b", "password": SMOKE_PASSWORD})
            assert rb.status_code == 201, f"第二个账号注册失败：{rb.status_code} {rb.text}"
            bob.headers["Authorization"] = f"Bearer {rb.json()['token']}"
            assert (await bob.get(f"{BASE}/api/bank/private/questions")).json()["total"] == 0
            assert (await bob.patch(
                f"{BASE}/api/bank/private/questions/{sorted(private_ids)[0]}", json={"status": "draft"}
            )).status_code == 404, "跨用户改到了别人的题"
            # 公共检索走真 Qdrant：私有题不进向量库，故关键词命中也拿不到 p_ 开头的题
            hits = (await bob.get(f"{BASE}/api/bank/questions",
                                  params={"q": "哈希表查找"})).json()["items"]
            assert not any(i["question_id"].startswith("p_") for i in hits), "私有题泄漏进公共检索"
            await bob.aclose()
            print("私有题隔离 OK：B 账号列表空 / 按 id 改 404 / 公共检索无 p_ 题")

            # 混入出题（真库 algorithms 只有 L2，L1 公共供给为 0）→ 候选池只可能是私有题，
            # 于是「私有题被检索到」这件事在真实 Qdrant + 真实 SQL 上是确定性的
            merged = await question_search.search_questions(
                domain="algorithms", difficulty="L1", k=10, user_id=me["id"]
            )
            assert {i["question_id"] for i in merged} == private_ids, \
                f"私有题未进候选池：{[i['question_id'] for i in merged]}"
            print(f"私有题混入出题 OK：algorithms/L1 无公共题，候选池 {len(merged)} 条全是私有题")

            # 编辑 → 归档 → 恢复（归档后不出现在「使用中」里，但仍在列表中可找回）
            one = sorted(private_ids)[0]
            edited = await client.patch(f"{BASE}/api/bank/private/questions/{one}",
                                        json={"answer": "改过的答案内容（smoke）。", "difficulty": "L2"})
            assert edited.status_code == 200, edited.text
            assert edited.json()["difficulty"] == "L2", "编辑未生效"
            assert (await client.patch(f"{BASE}/api/bank/private/questions/{one}",
                                       json={"status": "draft"})).json()["status"] == "draft"
            assert (await client.get(f"{BASE}/api/bank/private/questions",
                                     params={"status": "draft"})).json()["total"] == 1
            restored = (await client.patch(f"{BASE}/api/bank/private/questions/{one}",
                                           json={"status": "enabled"})).json()
            assert restored["status"] == "enabled" and restored["answer"] == "改过的答案内容（smoke）。"
            print("私有题管理 OK：编辑题干/答案/难度生效，归档 → 状态筛选 → 恢复，改动不丢")

            # 私有题计入容量（FR-14 × FR-13）：往有缺口的域补题，缺口应当被填上
            short_before = {(o["difficulty"], s["domain"]) for o in capacity for s in o["shortfalls"]}
            fill_doc = "\n\n".join(
                f"【题目】SMOKE 补位题 {n}：这道题用来验证私有题计入容量。\n"
                f"【答案】私有题的参考答案正文，第 {n} 条。" for n in (1, 2)
            )
            await client.post(f"{BASE}/api/bank/private/upload",
                              files={"file": ("补位.md", fill_doc.encode(), "text/markdown")},
                              data={"domain": "planning-reasoning", "difficulty": "L3"})
            cap_after = (await client.get(f"{BASE}/api/bank/capacity",
                                          params={"counts": "5,10,15"})).json()["options"]
            short_after = {(o["difficulty"], s["domain"]) for o in cap_after for s in o["shortfalls"]}
            assert short_after <= short_before, f"私有题只增不减，不足项反而变多：{short_after - short_before}"
            filled = short_before - short_after
            if ("L3", "planning-reasoning") in short_before:  # 真库当前唯一的缺口
                assert ("L3", "planning-reasoning") in filled, "L3 的缺口没被私有题填上"
            print(f"私有题计入容量 OK：不足项 {len(short_before)} → {len(short_after)}"
                  + (f"，补上 {filled}" if filled else "（真库当前无缺口，仅验证不倒退）"))

            # ── 难度锁定（P1-M6 FR-14）：固定 L3 的场次首题必须是 L3 ────────
            async with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={"position": "Agent/AI 工程师", "question_count": 5, "difficulty": "L3"},
            ) as r:
                assert r.status_code == 200, f"L3 场次创建失败: {r.status_code}"
                locked_events = await _events(r)
            locked_id = locked_events[0][1]["interview_id"]
            async with client.stream(
                "POST", f"{BASE}/api/interviews/{locked_id}/messages",
                json={"content": CANDIDATE_ANSWERS[0]},
            ) as r:
                assert r.status_code == 200, f"L3 场次作答失败: {r.status_code}"
                await _events(r)
            trace_locked = (await client.get(f"{BASE}/api/interviews/{locked_id}/trace")).json()
            first_ask = next(e for e in trace_locked["events"] if e["type"] == "ask")
            stored = db.get_interview(get_settings().db_path, locked_id)["difficulty"]
            assert (first_ask["detail"]["difficulty"], stored) == ("L3", "L3"), \
                f"难度未锁定：首题 {first_ask['detail']['difficulty']}、落库 {stored}"
            print(f"难度锁定 OK：L3 场次首题 "
                  f"[{domain_label(first_ask['detail']['domain'])}/L3]，落库 difficulty=L3\n")

            # 创建面试（SSE 开场）
            async with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={"position": "Agent/AI 工程师", "question_count": QUESTION_COUNT},
            ) as r:
                assert r.status_code == 200, f"创建失败: {r.status_code}"
                events = await _events(r)
            interview_id = events[0][1]["interview_id"]
            print(f"interview_id={interview_id}")
            for name, data in events:
                if name == "delta":
                    print(f"[开场] {data['text']}\n")

            # 逐轮发消息直到 done（角色扮演：自我介绍 → 逐题作答 → 反问）
            turn = 0
            done = False
            errors: list[dict] = []
            while not done and turn < QUESTION_COUNT * 3 + 6:
                answer = CANDIDATE_ANSWERS[turn] if turn < len(CANDIDATE_ANSWERS) else FALLBACK_ANSWER
                print(f"── 第 {turn + 1} 次作答 {'─' * 40}")
                print(f"[候选人] {answer}\n")
                async with client.stream(
                    "POST", f"{BASE}/api/interviews/{interview_id}/messages",
                    json={"content": answer},
                ) as r:
                    assert r.status_code == 200, f"消息失败: {r.status_code}"
                    events = await _events(r)
                for name, data in events:
                    if name == "delta":
                        print(f"[面试官] {data['text']}\n")
                    elif name == "question":
                        print(f"  >> 第 {data['index']} 题 [{data['domain']}/{data['difficulty']}]"
                              f" question_id={data['question_id']}")
                    elif name == "error":
                        # 静默忽略过 error 事件，结果「这一轮答了没反应」在日志里看不出来（实测踩到）
                        errors.append(data)
                        print(f"  ⚠ SSE error：{data['code']} · {data['message']}")
                    elif name == "done":
                        print(f"  >> 面试结束 report_ready={data['report_ready']}")
                        done = True
                turn += 1

                # 重连问候（P1-M4.7-D）：答题中刷新回来应附一句问候 + 当前题干，
                # 且只随本次响应返回、不落库（连续刷新不堆叠）
                if turn == 1 and not done:
                    plain = (await client.get(f"{BASE}/api/interviews/{interview_id}")).json()
                    back = (await client.get(
                        f"{BASE}/api/interviews/{interview_id}?reconnect=true"
                    )).json()
                    assert len(back["chat_history"]) == len(plain["chat_history"]) + 1, "重连未附问候"
                    greeting = back["chat_history"][-1]["content"]
                    assert "欢迎回来" in greeting, f"问候文案异常：{greeting}"
                    assert greeting.startswith("欢迎回来")
                    again = (await client.get(f"{BASE}/api/interviews/{interview_id}")).json()
                    assert again["chat_history"] == plain["chat_history"], "重连问候被写进了 checkpoint"
                    print(f"重连问候 OK（不落库）：{greeting[:48]}…\n")
            assert done, f"{turn} 轮后仍未结束"
            if errors:  # 真实 LLM 抖动（如结构化输出两次都不合法）会让某轮没有面试官回复
                print(f"⚠ 本场出现 {len(errors)} 次 SSE error（真人用户会看到错误提示 + 重试）："
                      + "；".join(f"第{e['code']}类 {e['message'][:40]}" for e in errors) + "\n")

            # 报告落库 + 接口
            r = await client.get(f"{BASE}/api/interviews/{interview_id}/report")
            assert r.status_code == 200
            report = r.json()["report"]
            print("=" * 60)
            print("报告摘要")
            print("=" * 60)
            print("五维均值:", report["scores"])
            print("域均分:", report["domain_scores"])
            print("短板:", report["weaknesses"])
            print("总评:", report["total_comment"])
            for item in report["study_advice"]:
                print(f"  学习建议 - {item['domain']}: {item['advice']}")
            print("逐题复盘（FR-25：我的回答 / 五维 / 关键点 / 参考答案）:")
            for item in report["per_question_comments"]:
                assert item["number"] is not None, "计入轮次的题型必须有序号"
                # 复盘扩展：已答题必须带回答与五维（标量 dict，不能是 Pydantic 对象）
                assert item["candidate_answer"], f"第{item['number']}轮缺我的回答"
                assert set(item["score"]) == {
                    "technical_depth", "fundamentals", "project_experience",
                    "communication", "problem_solving",
                }, f"第{item['number']}轮五维不全：{item['score']}"
                assert isinstance(item["covered_key_points"], list)
                # 题库题附参考答案全文；生成题/场景题无权威答案 → null
                if item["question_id"]:
                    assert item["reference_answer"], f"题库题缺参考答案：{item['question_id']}"
                    ref = "有参考答案"
                else:
                    assert item["reference_answer"] is None, "场景题/生成题不该有参考答案"
                    ref = "无参考答案（生成题）"
                print(f"  第{item['number']}轮 [{item['question_type']}/{item['domain']}] "
                      f"五维均分={sum(item['score'].values()) / 5:.1f} {ref} "
                      f"覆盖{len(item['covered_key_points'])}/遗漏{len(item['missed_key_points'])}"
                      f" · {item['comment'][:24]}…")

            # 决策回放（FR-21）：整场事件流一次取回，逐轮证据自包含
            r = await client.get(f"{BASE}/api/interviews/{interview_id}/trace")
            assert r.status_code == 200, f"回放查询失败: {r.status_code}"
            trace = r.json()
            events = trace["events"]
            types = [e["type"] for e in events]
            asked = [e["round"] for e in events if e["type"] == "ask"]
            assert trace["status"] == "finished"
            assert trace["answered_count"] == trace["question_count"] == QUESTION_COUNT
            assert types[0] == "ask" and types[-1] == "report", f"事件流首尾异常：{types}"
            assert asked == list(range(1, QUESTION_COUNT + 1)), f"出题轮次应为 1..question_count：{asked}"
            assert set(types) <= {"ask", "judge", "followup", "advance", "end_refused", "report"}
            print("=" * 60)
            print("决策回放（FR-21）：逐轮事件流")
            print("=" * 60)
            for e in events:
                d = e["detail"]
                if e["type"] == "ask":
                    print(f"  第{e['round']}轮 ASK      [{d['domain']}/{d['difficulty']}/{d['question_type']}] "
                          f"{'题库' if d['from_bank'] else '生成'} 候选{d['hits']} · {d['question'][:26]}…")
                elif e["type"] == "judge":
                    five = {k: d["score"][k] for k in
                            ("technical_depth", "fundamentals", "project_experience",
                             "communication", "problem_solving")}
                    print(f"  第{e['round']}轮 JUDGE    覆盖率={d['coverage']} "
                          f"难度={d['difficulty']}{'(变)' if d['difficulty_changed'] else ''} "
                          f"五维={list(five.values())}")
                elif e["type"] == "followup":
                    print(f"  第{e['round']}轮 FOLLOWUP 决策={d['decision']} 原因={d['reason']} "
                          f"· {d['text'][:22]}…")
                elif e["type"] == "advance":
                    print(f"  第{e['round']}轮 ADVANCE  换题原因={d['reason']} 阶段={d['phase']}")
                elif e["type"] == "end_refused":
                    print(f"  第{e['round']}轮 END_REFUSED {d['answered_count']}/{d['threshold']} 未达门槛")
                else:
                    print(f"  REPORT  {d['answered_count']}/{d['question_count']} 短板={d['weaknesses']}")
            # 回放三要素（SPEC §7）：每题有序号、轮次单调、追问决策与原因同源
            rounds = [e["round"] for e in events if e["round"] is not None]
            assert rounds == sorted(rounds), f"轮次非单调：{rounds}"
            assert all(e["detail"] for e in events), "事件 detail 不得为空"
            print(f"回放 OK：{len(events)} 个事件 / 轮次 1-{max(rounds)}")

            # 技术题同域成块（P1-M4.7-D）：域被切碎成散点就说明粘性失效；
            # 块内难度曲线是人工观测点——同域连问会不会一段卡在高难度
            blocks: list[list[dict]] = []
            for a in (e["detail"] for e in events if e["type"] == "ask"):
                if a["question_type"] == "scenario":  # 项目深挖题不参与同域粘性
                    continue
                if blocks and blocks[-1][0]["domain"] == a["domain"]:
                    blocks[-1].append(a)
                else:
                    blocks.append([a])
            domains = [b[0]["domain"] for b in blocks]
            assert len(domains) == len(set(domains)), f"同域未成块（域被切碎）：{domains}"
            assert sum(len(b) for b in blocks) == QUESTION_COUNT - project_count(QUESTION_COUNT)
            print("=" * 60)
            print("出题顺序（P1-M4.7-D）：技术题同域成块 + 块内难度曲线")
            print("=" * 60)
            for b in blocks:
                src = "/".join("题库" if a["from_bank"] else "生成" for a in b)
                print(f"  {domain_label(b[0]['domain']):<20} "
                      f"{' → '.join(a['difficulty'] for a in b):<12} {len(b)} 题（{src}）")
            print(f"  项目深挖题 {project_count(QUESTION_COUNT)} 道（前置，不参与同域成块）")

            # Langfuse（P1-M4）：云端「按场次可查」——把观测读回来核对，不靠肉眼
            if observability.enabled():
                lf = observability.get_client()
                trace_id = lf.create_trace_id(seed=interview_id)
                observability.flush()
                report_model = get_settings().deepseek_pro_model
                obs = await _fetch_observations(lf, trace_id, report_model=report_model)
                turns = [o for o in obs if o.name == "interview-turn"]
                gens = [o for o in obs if o.type == "GENERATION"]
                shapes = [(o.name, o.type) for o in obs]
                assert len(turns) >= QUESTION_COUNT, f"轮次 span 少于 {QUESTION_COUNT} 个（每轮一个）：{shapes}"
                assert len(gens) >= QUESTION_COUNT * 2, \
                    f"generation 少于 {QUESTION_COUNT * 2} 个（每轮至少两次 LLM 调用）：{shapes}"
                assert {o.session_id for o in obs} == {interview_id}, "session_id 与场次不一致"
                assert {o.user_id for o in obs} == {me["id"]}, "user_id 与账号不一致"
                turn_ids = {t.id for t in turns}
                assert all(g.parent_observation_id in turn_ids for g in gens), \
                    "generation 未挂在轮次 span 下"
                models = {_model_of(g) for g in gens}
                assert models and None not in models, f"generation 未带模型名（成本归属前提）：{models}"
                assert report_model in models, f"报告未走深度档 {report_model}（SPEC §3）：{models}"
                tokens = sum((g.usage_details or {}).get("total", 0) for g in gens)
                cost = sum((g.cost_details or {}).get("total", 0) for g in gens)
                print(f"Langfuse OK：trace_id={trace_id} session_id={interview_id}")
                print(f"  轮次 span {len(turns)} 个 / generation {len(gens)} 个 "
                      f"（模型 {sorted(models)}，token {tokens}，成本 ¥{cost:.4f}）")
                if cost == 0:  # 成本靠价格表匹配，配不配不属于代码验收项，给条提示就好
                    print("  ⚠ 成本为 0：Langfuse 项目里没有这些模型的价格定义"
                          "（Settings → Models 按 DeepSeek 官方价目加一条即可）")
            else:
                print("Langfuse 未配置（.env 缺 LANGFUSE_* key）→ 跳过云端核对")

            # 会话恢复 + 历史列表
            r = await client.get(f"{BASE}/api/interviews/{interview_id}")
            session = r.json()
            assert session["report_ready"] is True
            assert len(session["chat_history"]) > 0
            r = await client.get(f"{BASE}/api/interviews")
            rows = r.json()
            assert any(x["id"] == interview_id and x["status"] == "finished" for x in rows)
            print(f"会话恢复 OK（{len(session['chat_history'])} 条消息），历史列表 {len(rows)} 场")

            # 结束陈词（P1-M4.7-D，D4）：LLM 生成，红线「不点分数、不评域强弱」——
            # 结构上已保证拿不到报告文字（模板无输入），这里把成品打出来人工读一遍
            closing = session["chat_history"][-1]
            assert closing["role"] == "assistant" and closing["content"], "结束陈词缺失"
            mentioned = [lab for lab in DOMAIN_LABELS.values() if lab in closing["content"]]
            leaked = [lab for lab in map(domain_label, report["weaknesses"]) if lab in mentioned]
            print("=" * 60)
            print("结束陈词（D4）")
            print("=" * 60)
            print(f"  {closing['content']}")
            print(f"  红线自查：{'⚠ 点了短板域 ' + '/'.join(leaked) if leaked else '未提短板域'}"
                  f"／{'⚠ 提到了域 ' + '/'.join(mentioned) if mentioned else '未提任何域'}"
                  f"／{'⚠ 复述了总评原句' if report['total_comment'][:12] in closing['content'] else '未复述总评'}")

        # 业务库落库验证（answers 行数 = 已答题数 = 全场轮次）
        with __import__("sqlite3").connect(get_settings().db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM answers WHERE interview_id=?", (interview_id,)
            ).fetchone()[0]
        assert count == trace["question_count"], f"落库 {count} 行 ≠ 轮次 {trace['question_count']}"
        row = db.get_interview(get_settings().db_path, interview_id)
        assert row["user_id"] == me["id"], "场次未归属到当前用户"
        print(f"落库 OK：answers {count} 行，interviews status={row['status']}，"
              f"归属 user_id={row['user_id'][:8]}…")
        print("\nsmoke 完成 ✓")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(main())
