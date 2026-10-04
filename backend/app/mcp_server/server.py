"""题库查询 MCP server（P2-M10）：把题库能力暴露给任何 MCP 客户端。

三条设计约束（SPEC §7「MCP 通道」）：

- **只读 + 只公共题**：每个查询都经 `app.tools.bank_query`（它的每个查询自带
  `user_id IS NULL`）与 `hybrid_search`（私有题不进 Qdrant，检索天然只见公共题）。
  私有题库是某个账号的上传内容，**不从这个出口出去**；
- **协议代码只在本包**：领域逻辑不重写（题库页与出题检索用的就是这两个模块），
  换规范版本 / 换传输只动这里；
- **stdio only**：本地进程。无鉴权的 HTTP 传输会把题库（含个人来源的文本）暴露到网络。

**错误如实回报**：工具内异常转 `ToolError("人话原因")`——探针实测（mcp 2.3.0），未捕获的
异常会被 SDK 换成「Error executing tool X」，原始信息只留在服务端日志里，**模型看不到原因**，
那等于让模型对着「工具坏了」瞎猜（重试 / 换参数都无从谈起）。

规范版本：**2026-07-28**（`mcp>=2,<3` 的 SDK 与客户端协商出的就是这一版，探针实测）。
"""

from __future__ import annotations

import asyncio
import sqlite3

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain import ASKABLE_DOMAINS, DOMAIN_LABELS, ENABLED_DOMAINS
from app.tools import bank_query, hybrid_search

SEARCH_LIMIT_MAX = 20

_DOMAINS = "、".join(
    f"{key}（{DOMAIN_LABELS[key]}）"
    for key in sorted(ENABLED_DOMAINS | ASKABLE_DOMAINS)
    if key in DOMAIN_LABELS
)
_DOMAIN_DOC = f"可选知识域：{_DOMAINS}"
_DIFFICULTY_DOC = "难度：L1（概念与名词）/ L2（原理、对比与选型）/ L3（底层实现与设计权衡）"

mcp = MCPServer(
    "marda-bank",
    version="0.1.0",
    instructions=(
        "码达（Marda）面试题库查询，只读。题库面向 Agent/AI 工程师岗位，"
        "题目带参考答案、关键点与来源（开源许可四要素）。"
        "检索用 search_questions（语义），浏览/取单题用 browse_questions / get_question（纯 SQL）。"
    ),
)


class SourceItem(BaseModel):
    """来源四要素（合规口径，SPEC §8.1）：一题可多源，首条为答案主源。"""

    source: str
    license: str
    url: str | None = None
    source_detail: str | None = None


class QuestionItem(BaseModel):
    question_id: str = Field(description="题目 id（内容哈希；get_question 用它）")
    question: str
    answer: str = Field(description="参考答案")
    key_points: list[str] = Field(default_factory=list, description="评分用的关键点")
    follow_ups: list[str] = Field(default_factory=list)
    domain: str
    topic: str
    difficulty: str
    company: str | None = Field(default=None, description="来源公司与面次（题库元数据，可空）")
    round: str | None = None
    sources: list[SourceItem] = Field(default_factory=list)


class QuestionList(BaseModel):
    total: int | None = Field(
        default=None, description="命中总数（浏览模式有；检索按相关性排序、不翻页，故为 null）"
    )
    items: list[QuestionItem]


def _items(raw: list[dict]) -> list[QuestionItem]:
    """领域 dict → 工具出参模型（多余键由 pydantic 默认忽略；sources 已由 attach_sources 挂好）。"""
    return [QuestionItem(**item) for item in raw]


def _sql_error(exc: Exception, db_path: object) -> ToolError:
    """题库读数失败（文件不在 / 没建表 / 读不动）→ 给出去路，别只说「工具坏了」。"""
    return ToolError(f"题库读数失败（{type(exc).__name__}: {exc}）——数据库在 {db_path}，核对路径与建表状态")


@mcp.tool()
async def search_questions(
    query: str,
    domain: str | None = None,
    difficulty: str | None = None,
    limit: int = 5,
) -> QuestionList:
    """按语义检索题库（混合检索：dense + sparse 融合，短查询保留重排）。

    适合「想找某主题的题」：query 用中文自然语言（例：「ReAct 和 Plan-and-Execute 的区别」）。
    需要 Qdrant 与嵌入服务在跑；没起时改用 browse_questions（纯 SQL，不依赖向量服务）。
    """
    limit = max(1, min(limit, SEARCH_LIMIT_MAX))
    settings = get_settings()
    try:
        items = await hybrid_search.hybrid_search(
            query.strip(),
            k=limit,
            filters={"domain": domain, "difficulty": difficulty},
            db_path=settings.db_path,
        )
    except ValueError as exc:  # hybrid_search 对空查询抛 ValueError
        raise ToolError(f"查询文本不合法：{exc}") from exc
    except Exception as exc:  # Qdrant / 嵌入服务不可达（httpx、qdrant_client 各自的异常类型）
        raise ToolError(
            f"检索服务不可用（{type(exc).__name__}: {exc}）。检索需要 Qdrant 与嵌入容器"
            "（docker compose up -d qdrant embedding）；不启动也能用 browse_questions 浏览题库。"
        ) from exc
    # 挂来源四要素是同步 SQLite 读，别卡事件循环
    await asyncio.to_thread(bank_query.attach_sources, settings.db_path, items)
    return QuestionList(items=_items(items))


@mcp.tool(description=f"按维度浏览题库（纯 SQL，不依赖向量服务）：分页取题并返回总数。\n{_DOMAIN_DOC}\n{_DIFFICULTY_DOC}")
def browse_questions(
    domain: str | None = None,
    difficulty: str | None = None,
    company: str | None = None,
    round: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> QuestionList:
    settings = get_settings()
    try:
        items, total = bank_query.browse_questions(
            settings.db_path,
            filters={"domain": domain, "difficulty": difficulty, "company": company,
                     "round": round},
            page=page,
            page_size=page_size,
        )
        bank_query.attach_sources(settings.db_path, items)
    except (sqlite3.Error, OSError) as exc:
        raise _sql_error(exc, settings.db_path) from exc
    return QuestionList(total=total, items=_items(items))


@mcp.tool()
def get_question(question_id: str) -> QuestionItem:
    """按 id 取一道题的完整内容（题干 / 参考答案 / 关键点 / 追问 / 来源四要素）。

    只覆盖公共题：私有题与已归档题一律「没找到」（不区分原因——别人的私有题 id 不该可探测）。
    """
    settings = get_settings()
    try:
        item = bank_query.get_question(settings.db_path, question_id)
        if item is not None:
            bank_query.attach_sources(settings.db_path, [item])
    except (sqlite3.Error, OSError) as exc:
        raise _sql_error(exc, settings.db_path) from exc
    if item is None:
        raise ToolError(f"题库里没有这道题：{question_id}（不是公共题、已归档，或 id 打错）")
    return QuestionItem(**item)
