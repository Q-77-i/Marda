"""结构化输出 schema（agents 层）。

ScoreItem 定义在 graph/state.py（SPEC §4.1 归属 state），这里放其余节点输出：
自我介绍提炼、LLM 生成题、报告文字部分。
"""

from __future__ import annotations

from pydantic import BaseModel


class ProfileExtraction(BaseModel):
    """自我介绍提炼（供场景题定制与报告生成）。"""

    summary: str = ""
    projects: list[str] = []
    tech_stack: list[str] = []


class GeneratedQuestion(BaseModel):
    """LLM 生成题（题库未命中兜底 / 场景题，PRD §4.3「按同标准生成」）。"""

    text: str
    topic: str
    key_points: list[str]
    answer: str


class PerQuestionComment(BaseModel):
    question_id: str
    comment: str


class StudyAdvice(BaseModel):
    domain: str
    advice: str


class ReportLLM(BaseModel):
    """报告文字部分（SPEC §4.6：总评 + 逐题点评 + 学习建议）。"""

    total_comment: str
    per_question_comments: list[PerQuestionComment]
    study_advice: list[StudyAdvice]
