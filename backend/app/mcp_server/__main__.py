"""stdio 入口：`cd backend && uv run python -m app.mcp_server`。

stdio 是**刻意选定的唯一传输**：MCP 客户端在本机把这个进程当子进程起，题库不经过网络。
（无鉴权的 HTTP 传输会把题库——含个人来源的文本——暴露给任何能连上的进程。）
"""

from app.mcp_server.server import mcp

if __name__ == "__main__":
    mcp.run()  # 缺省 transport="stdio"
