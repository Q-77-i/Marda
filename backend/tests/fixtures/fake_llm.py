"""图集成测试用 FakeLLM：按 prompt 标记路由，注入方式与 test_llm.py 同模式。

路由标记与 agents/prompts.py 的模板文案一一对应（改 prompt 时保持这些标记词）：
- 「评分官」→ score 工厂 JSON（callable 可编程驱动追问/难度路径）
- 「报告官」→ report JSON
- 「出题官」→ generated JSON（题库未命中兜底 / 场景题）
- 含「提炼」→ profile JSON（自我介绍提炼）
- 其余（开场/出题文案/追问/反问/挽留）→ 固定文案
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

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


class FakeLLMClient:
    """chat.completions.create 的 fake（经 app.llm._get_client 注入）。"""

    def __init__(
        self,
        *,
        score: dict | Callable[[], dict] | None = None,
        profile: dict | None = None,
        generated: dict | None = None,
        report: dict | None = None,
        text: str = "面试官文案",
    ) -> None:
        self._score = score if score is not None else DEFAULT_SCORE
        self._profile = profile or DEFAULT_PROFILE
        self._generated = generated or DEFAULT_GENERATED
        self._report = report or DEFAULT_REPORT
        self._text = text
        self.calls: list[dict] = []

    @property
    def chat(self):
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                return outer._create(**kwargs)

        return SimpleNamespace(completions=_Completions())

    def _create(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        self.calls.append({"system": system})
        if "评分官" in system:
            score = self._score() if callable(self._score) else self._score
            content = json.dumps(score, ensure_ascii=False)
        elif "报告官" in system:
            content = json.dumps(self._report, ensure_ascii=False)
        elif "出题官" in system:
            content = json.dumps(self._generated, ensure_ascii=False)
        elif "提炼" in system:
            content = json.dumps(self._profile, ensure_ascii=False)
        else:
            content = self._text
        return _response(content)
