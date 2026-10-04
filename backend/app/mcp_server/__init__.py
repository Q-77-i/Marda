"""MCP 题库查询 server（P2-M10）——**协议代码的唯一住所**。

领域逻辑全在既有 tools（`bank_query` / `hybrid_search`，与 HTTP API 用的是同一批函数），
本包只做「MCP 工具 ↔ 领域函数」的胶水：换 MCP 规范版本只动这里，`mcp` 依赖也只需装在
跑 server 的环境里（api 镜像 `uv sync --no-dev` 不带它）。

跑法（stdio）：
    cd backend && uv run python -m app.mcp_server
接到客户端（Claude Code）：
    claude mcp add marda-bank -- <backend>/.venv/bin/python -m app.mcp_server
（工作目录要在 backend/ 下，或用 `--directory` 指过去——`app` 是相对导入的包名。）
"""

from app.mcp_server.server import mcp

__all__ = ["mcp"]
