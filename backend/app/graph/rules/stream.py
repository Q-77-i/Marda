"""面试官消息的流式透传（P2-M4）：节点把文案送进 SSE 流的唯一出口。

三条口径：

- **走 LangGraph custom 流**（`get_stream_writer`），不自建队列：状态机与图结构一条不动，
  service 只是多收一路 `stream_mode`；
- **只发领域形状** `{type: message_start|message_delta, text}`：SSE 事件名（delta_start /
  delta_chunk）是 transport 的词汇，映射归 service——M5 的语音事件同样只在这里加类型；
- **不变量：发出的分片拼接 == 节点落 chat_history 的那条消息**。`speak` 返回全文就是
  为了让它当 `add_history` 的入参——节点别再自己拼一遍（拼错分片与终稿就打架）。

图外（单测直调节点 / evals / `ainvoke` 非流式）`get_stream_writer()` 会抛 RuntimeError，
这里统一吞掉退化为空操作——节点代码因此不必分支「我在不在图里」。
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

from app import llm


def _emit(payload: dict) -> None:
    """发一条流事件；图外为空操作（`get_stream_writer` 图外抛 RuntimeError，实测）。"""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer(payload)


def begin(text: str = "") -> None:
    """开一条面试官消息；text 是纯代码文案（模板衔接语 / 题库追问元数据）时作为首块发出。"""
    _emit({"type": "message_start"})
    if text:
        _emit({"type": "message_delta", "text": text})


async def speak(messages: list[dict[str, Any]], *, preamble: str = "", **kwargs: Any) -> str:
    """生成一条面试官消息：preamble（代码前缀）先发，LLM 分片随后透传。

    返回**应落 chat_history 的全文**（preamble + LLM 文案）——调用方直接拿它 add_history。
    """
    begin(preamble)
    text = await llm.chat(
        messages,
        on_delta=lambda piece: _emit({"type": "message_delta", "text": piece}),
        **kwargs,
    )
    return f"{preamble}{text}"
