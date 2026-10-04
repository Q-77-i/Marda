"""MCP 题库查询 server 的真链路 smoke（P2-M10）：把 server 当**子进程**起，用真客户端连。

用法：cd backend && uv run python scripts/smoke_mcp.py

它证明的是「**展示点可连**」这句话的字面意思：stdio 子进程 + 真握手（协议版本 2026-07-28）
+ 三个工具各调一次。集成测试（tests/integration/test_mcp_server.py）走的是 in-memory
传输——协议编解码是真的，但没有进程；这条把进程、stdio 帧、真实业务库（只读）都带上。

库 = `data/marda.sqlite3`（**只读**；业务库是 rollback journal，宿主机读安全——
WAL 那条纪律是 checkpoints 库的事）。检索（search_questions）需要 Qdrant + 嵌入容器：
没起时**如实打印「未验证」并继续**（同 smoke_api 对 Qdrant 的口径），不起也会红的是别的问题。

输出**只打印 id / 计数 / 来源名，不打印题干与答案**——smoke 的输出会被粘进会话与文档，
而题库原文（尤其个人来源的）不能进仓库（红线，见 CLAUDE.md）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client, StdioServerParameters  # noqa: E402

from app.config import get_settings  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SEARCH_HINT = "检索服务不可用"  # server 侧的「Qdrant/嵌入没起」文案前缀


def _payload(result) -> dict:
    assert result.is_error is False, f"{result.content[0].text if result.content else result}"
    return result.structured_content


def _private_question_id(db_path: Path) -> str | None:
    """真库里随便一道私有题（用于「不可见」反查）；没有就返回 None。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT id FROM questions WHERE user_id IS NOT NULL AND status='enabled' LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


async def main() -> None:
    settings = get_settings()
    db_path = settings.db_path
    assert db_path.exists(), f"业务库不存在：{db_path}"

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(BACKEND)
    )
    print("=" * 60)
    print(f"MCP smoke：stdio 子进程连 marda-bank，库 {db_path}（只读）")
    print("=" * 60)

    async with Client(params) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        print(f"协议版本 {client.protocol_version} · 工具 {sorted(tools)}")

        browsed = _payload(await client.call_tool("browse_questions", {"domain": "rag", "page_size": 3}))
        assert browsed["total"] > 0 and browsed["items"], "rag 域浏览为空（库不对？）"
        first = browsed["items"][0]
        assert first["answer"] and first["key_points"], "题目缺答案/关键点"
        print(f"browse OK：rag 共 {browsed['total']} 题，取回 {len(browsed['items'])} 条"
              f"（首条 {first['question_id'][:12]}… 难度 {first['difficulty']}，"
              f"来源 {[s['source'] for s in first['sources']]}）")

        one = _payload(await client.call_tool("get_question", {"question_id": first["question_id"]}))
        assert one["question_id"] == first["question_id"] and one["answer"]
        assert len(one["question"]) > 5, "题干异常短"
        print(f"get_question OK：{one['question_id'][:12]}…（题干 {len(one['question'])} 字，"
              f"关键点 {len(one['key_points'])} 条）")

        private = _private_question_id(db_path)
        if private:
            miss = await client.call_tool("get_question", {"question_id": private})
            assert miss.is_error is True, "私有题被 get_question 拿到了（隔离破了）"
            print(f"私有题不可见 OK：真库那道私有题（{private[:8]}…）返回「没有这道题」")
        else:
            print("⚠ 真库当前没有启用的私有题 → 「私有题不可见」本轮未在真库上验证"
                  "（集成测试已覆盖该断言）")

        search = await client.call_tool("search_questions", {"query": "ReAct 和 Plan-and-Execute 的区别", "limit": 3})
        if search.is_error:
            text = search.content[0].text
            assert SEARCH_HINT in text, f"检索失败但不是「服务没起」：{text}"
            print("⚠ 检索未验证：Qdrant / 嵌入容器没起（docker compose up -d qdrant embedding 后可复跑）")
        else:
            data = search.structured_content
            assert data["items"], "检索返回空（题库/向量库对不上？）"
            top = data["items"][0]
            print(f"search OK：命中 {len(data['items'])} 条（首条 {top['question_id'][:12]}… "
                  f"{top['domain']}/{top['difficulty']}，来源 {[s['source'] for s in top['sources']]}）")

    print("\n✅ MCP 展示点可连：stdio 子进程 + 真握手 + 三工具（详见上方逐条）")


if __name__ == "__main__":
    asyncio.run(main())
