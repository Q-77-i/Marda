"""结构化输出 schema（agents 层）。

ScoreItem 定义在 graph/state.py（SPEC §4.1 归属 state），这里放其余节点输出：
自我介绍提炼、LLM 生成题、报告文字部分。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


# ---- 代码工作台（M15）：LLM 结构化点评 ----


class CodeIssue(BaseModel):
    """单条代码问题（M15）：行号可缺省（模型给不出准确行号时宁可没有）。"""

    severity: Literal["error", "warning", "suggestion"] = "warning"
    line: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=800)
    fix: str = Field(min_length=1, max_length=800)


class CodeEdgeCase(BaseModel):
    """具体反例（M15）：input/expected 必须是可复现的实例，不接受「注意边界」这类空话。"""

    input: str = Field(min_length=1, max_length=300)
    expected: str = Field(min_length=1, max_length=300)
    note: str = Field(default="", max_length=300)


class CodeComplexity(BaseModel):
    """候选代码实际复杂度判断（M15）。"""

    time: str = Field(min_length=1, max_length=100)
    space: str = Field(min_length=1, max_length=100)
    correct: bool
    note: str = Field(default="", max_length=300)


class CodeReview(BaseModel):
    """代码点评结构化输出（M15）：至少给一条具体反例，追问只问一个最关键的问题。"""

    issues: list[CodeIssue] = Field(default_factory=list, max_length=15)
    edge_cases: list[CodeEdgeCase] = Field(min_length=1, max_length=8)
    complexity: CodeComplexity
    follow_up: str = Field(min_length=1, max_length=400)
