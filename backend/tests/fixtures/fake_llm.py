"""图集成测试用 FakeLLM：按 prompt 标记路由，注入方式与 test_llm.py 同模式。

路由标记与 agents/prompts.py 的模板文案一一对应（改 prompt 时保持这些标记词）：
- 「评分官」→ score 工厂 JSON（callable 可编程驱动追问/难度路径）；
  同一标记下再按「行为面」分技术面/行为面两套默认分（P1-M11）
- 「报告官」→ report JSON
- 「出题官」→ generated JSON（题库未命中兜底 / 场景题 / 行为面生成题）
- 含「提炼」→ profile JSON（自我介绍提炼）
- 含「真诚收尾」→ 结束陈词文案（P1-M4.7-D）
- 其余（开场/出题文案/追问/反问/挽留）→ 固定文案
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

DEFAULT_CLOSING = "今天的面试就到这里，感谢你的时间，报告已经生成。"

DEFAULT_SCORE = {
    "technical_depth": 4,
    "fundamentals": 4,
    "project_experience": 4,
    "communication": 4,
    "problem_solving": 4,
    "covered_key_points": ["k1", "k2"],
    "missed_key_points": [],
    "error_flag": False,
    "comment": "整体不错",
}

# 行为面五维（P1-M11，键与 aggregate.BEHAVIORAL_DIMS 一致）
DEFAULT_BEHAVIORAL_SCORE = {
    "communication": 4,
    "logic_structure": 4,
    "project_experience": 4,
    "values_motivation": 3,
    "career_stability": 5,
    "covered_key_points": ["k1"],
    "missed_key_points": [],
    "error_flag": False,
    "comment": "讲述清晰",
}
DEFAULT_PROFILE = {"summary": "应届生，Agent 方向", "projects": ["做过 RAG 问答系统"], "tech_stack": ["Python"]}
DEFAULT_GENERATED = {
    "text": "请设计一个带工具调用的 Agent 系统",
    "topic": "系统设计",
    "key_points": ["架构分层", "容错设计"],
    "answer": "参考答案",
}
DEFAULT_REPORT = {
    "total_comment": "整体表现良好",
    "per_question_comments": [{"question_id": "q1", "comment": "回答到位"}],
    "study_advice": [{"domain": "rag", "advice": "深入检索"}],
}


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


STREAM_PIECE_SIZE = 3  # 流式分片粒度：模拟真 token 流的「多片」（数值无意义，只要 >1 片）


def _delta_chunk(text: str) -> SimpleNamespace:
    """流式正文分片（openai AsyncStream 的 chunk 形状）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None
    )


def _usage_chunk() -> SimpleNamespace:
    """结尾的 usage 块：choices 为空、只有 usage（P2-M4 开 include_usage 后必有）。"""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    )


def stream_of(text: str, *, piece_size: int = STREAM_PIECE_SIZE):
    """把整段文本包成流式响应（openai AsyncStream 形状）；各测试替身共用同一套切片。"""
    async def _gen():
        for i in range(0, len(text), piece_size):
            yield _delta_chunk(text[i:i + piece_size])
        yield _usage_chunk()

    return _gen()


class FakeLLMClient:
    """chat.completions.create 的 fake（经 app.llm._get_client 注入）。"""

    def __init__(
        self,
        *,
        score: dict | Callable[[], dict] | None = None,
        behavioral_score: dict | Callable[[], dict] | None = None,
        profile: dict | None = None,
        generated: dict | None = None,
        report: dict | None = None,
        text: str = "面试官文案",
        closing: str = DEFAULT_CLOSING,
    ) -> None:
        self._score = score if score is not None else DEFAULT_SCORE
        self._behavioral_score = (
            behavioral_score if behavioral_score is not None else DEFAULT_BEHAVIORAL_SCORE
        )
        self._profile = profile or DEFAULT_PROFILE
        self._generated = generated or DEFAULT_GENERATED
        self._report = report or DEFAULT_REPORT
        self._text = text
        self._closing = closing
        self.calls: list[dict] = []

    @property
    def chat(self):
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                if kwargs.get("stream"):
                    return outer._stream(**kwargs)  # 异步生成器对象（await 后可直接 async for）
                return outer._create(**kwargs)

        return SimpleNamespace(completions=_Completions())

    async def _stream(self, **kwargs):
        """流式分支（P2-M4）：按固定粒度切片产出，结尾补 usage 块。

        切片的粒度是刻意的「多片且非等长于全文」——单片的流式在协议上等价于旧 delta，
        测不出边界（前端分片拼接、事件序断言都需要真的多片）。
        """
        async for chunk in stream_of(self._content(**kwargs)):
            yield chunk

    def _create(self, **kwargs):
        return _response(self._content(**kwargs))

    def _content(self, **kwargs) -> str:
        system = kwargs["messages"][0]["content"]
        # messages 全量留档（P2-M6）：图附件断言要看得到整条消息列表，不只 system
        self.calls.append({"system": system, "messages": kwargs.get("messages", [])})
        if "评分官" in system:
            raw = self._behavioral_score if "行为面" in system else self._score
            return json.dumps(raw() if callable(raw) else raw, ensure_ascii=False)
        if "报告官" in system:
            return json.dumps(self._report, ensure_ascii=False)
        if "出题官" in system:
            return json.dumps(self._generated, ensure_ascii=False)
        if "提炼" in system:
            return json.dumps(self._profile, ensure_ascii=False)
        if "真诚收尾" in system:
            return self._closing
        return self._text
