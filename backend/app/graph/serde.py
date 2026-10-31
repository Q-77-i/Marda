"""checkpoint 序列化：zlib 压缩 + 新旧 type 双读（P4-E1）。

为什么压：checkpointer 每个 super-step 存一张**全量快照**，`trace_log` /
`answered_questions` / `chat_history` 三个通道都只增不改，于是单张快照随轮次变大、
累计近似 O(n²)（实测 15 题场 134 张 / 11.5MB）。压缩是**无损**的一刀：先解决
「越跑越大」，再谈状态外置。

压缩口径：
- `dumps_typed`：先用 LangGraph 的 JsonPlusSerializer 编码，再 zlib 压一遍，
  type 写成 `msgpack+zlib`（`json+zlib` 同理）；
- `loads_typed`：type 以 `+zlib` 结尾就先解压再走原逻辑；**旧行（`msgpack`/`json`）
  照常可读**——不兼容就等于老场次全废，比膨胀更糟。

为什么不改 state schema / 不做状态外置：那是更大的手术（要重写节点读写口径），
收益与风险都不是这一轮要的。先压缩，测出真实体积，再决定要不要外置。
"""

from __future__ import annotations

import zlib

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

ZLIB_SUFFIX = "+zlib"
# 这些 type 本来就是裸字节/空值，压了反而变大
_NO_COMPRESS = frozenset({"null", "bytes", "bytearray"})


class ZlibJsonPlusSerializer(JsonPlusSerializer):
    """JsonPlusSerializer + zlib 压缩。

    只覆写编解码两个方法，其余（allowlist、msgpack ext hook）全部继承——
    序列化语义与 LangGraph 默认实现**逐字一致**，只是多了一层压缩。
    """

    def dumps_typed(self, obj: object) -> tuple[str, bytes]:
        type_, payload = super().dumps_typed(obj)
        if type_ in _NO_COMPRESS:
            return type_, payload
        return type_ + ZLIB_SUFFIX, zlib.compress(payload, 6)

    def loads_typed(self, data: tuple[str, bytes]) -> object:
        type_, payload = data
        if type_.endswith(ZLIB_SUFFIX):
            type_ = type_[: -len(ZLIB_SUFFIX)]
            payload = zlib.decompress(payload)
        return super().loads_typed((type_, payload))


def make_serde() -> ZlibJsonPlusSerializer:
    """构造 checkpointer 用的序列化器。

    显式允许 state 模块的 Pydantic 类型（Phase/ScoreItem/BehavioralScoreItem/
    QuestionRecord/InterviewState），否则 LangGraph 反序列化告警且未来版本会阻断。
    """
    from app.graph.state import (
        BaseScore,
        BehavioralScoreItem,
        InterviewState,
        Phase,
        QuestionRecord,
        ScoreItem,
    )

    return ZlibJsonPlusSerializer(
        allowed_msgpack_modules=[
            InterviewState, Phase, QuestionRecord, ScoreItem, BehavioralScoreItem, BaseScore
        ]
    )
