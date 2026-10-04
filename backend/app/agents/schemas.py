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


class ResumeExtraction(BaseModel):
    """简历抽取（P2-M11）：预填 candidate_profile 用——出题侧零改动就变具体。

    只有结构化结果没有原文：原文留在 `resumes.text` 供重新解析，不随接口出行。
    """

    summary: str = ""
    projects: list[str] = []
    skills: list[str] = []


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
