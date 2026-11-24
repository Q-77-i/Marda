"""测试夹具：re-export 应用级 FakeLLM（J6-0 起唯一实现是 `app.llm_fake`）。

为什么不再维护第二份：图/Agent 的 fake 路由（评分官、报告官、出题官、行为面判定）
是同一套语义；演练与测试各写一份，改 prompt 时必然漂移。真实现放在 app 里，
故障演练（`LLM_MODE=fake`）与测试夹具共用。
"""

from app.llm_fake import (  # noqa: F401
    DEFAULT_BEHAVIORAL_SCORE,
    DEFAULT_CLOSING,
    DEFAULT_CODE_REVIEW,
    DEFAULT_CODING_ANSWER,
    DEFAULT_GENERATED,
    DEFAULT_PROFILE,
    DEFAULT_REPORT,
    DEFAULT_RESUME,
    DEFAULT_SCORE,
    STREAM_PIECE_SIZE,
    FakeLLMClient,
    stream_of,
)
