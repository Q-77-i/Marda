"""面试官衔接语（P1-M4.7-D 人味层，SPEC §4.8）：确定性衔接语 + 时长插槽。

六类黏合点里高频短衔接全部用模板 + 插槽（零 LLM、可单测、可回放）：
开场过渡 / 项目续题 / 转技术 / 题间同域 / 跨域换方向 / 答错缓冲 / 重连问候；
低频长文（开场白、结束陈词）走 LLM（intro / report 节点）。

落点约定（P1-M4.7 拍板）：衔接语与题目同一条消息（ask 节点 prepend）；
**答错缓冲例外**——它回应的是上一题，独立成条，避免「新题开头带着对上一题的评价」。

变体按轮次（answered_count）确定性轮换：同一场次同样输入永远同样文案，回放可重算。
"""

from __future__ import annotations

from enum import Enum

from app.domain import DOMAIN_LABELS
from app.graph.state import InterviewState, Phase, QuestionRecord

MINUTES_PER_QUESTION = 3  # 开场白时长插槽：每题按 3 分钟估（含追问）
PROJECT_DOMAIN = "project"  # 项目深挖题域（不参与域统计，见 domain.project_count）
PROJECT_DOMAIN_LABEL = "项目深挖"


class TransitionKind(str, Enum):
    """衔接分类（六类黏合点里「阶段过渡 / 题间衔接」的细分）。"""

    OPEN_PROJECT = "open_project"  # 首题：WARMUP → PROJECT
    PROJECT_NEXT = "project_next"  # 项目段续题（换切入点）
    TO_TECH = "to_tech"  # PROJECT → TECH_BASE
    SAME_DOMAIN = "same_domain"  # 技术段同域续问（同域成块，P1-M4.7-D2）
    SWITCH_DOMAIN = "switch_domain"  # 技术段跨域换方向


_VARIANTS: dict[TransitionKind, tuple[str, ...]] = {
    TransitionKind.OPEN_PROJECT: (
        "谢谢你的自我介绍。那我们先从你的项目聊起，看几道设计题。",
        "好的，了解了。接下来我们结合你的项目经历，聊几个实际的设计问题。",
    ),
    TransitionKind.PROJECT_NEXT: (
        "好，那我们换个角度，再看一个项目相关的问题。",
        "嗯，我再问一个偏设计的问题。",
    ),
    TransitionKind.TO_TECH: (
        "项目聊得差不多了，下面我们看几个技术问题。",
        "好，项目部分先到这里，接下来聊几个技术点。",
    ),
    TransitionKind.SAME_DOMAIN: (
        "嗯，我们接着往下看。",
        "好，那顺着这个话题再问一个。",
    ),
    TransitionKind.SWITCH_DOMAIN: (
        "好，我们换个方向，聊聊{label}。",
        "嗯，接下来换个领域，看看{label}方面。",
    ),
}

# 答错缓冲：只表达接纳、不指方向（方向交给下一题的衔接语，否则「换个方向」说两遍）
_BUFFER_VARIANTS = (
    "没关系，这道题我们先放一放。",
    "没关系，这个问题先到这里，我们继续。",
)


def estimated_minutes(question_count: int) -> int:
    """面试预计时长（分钟，开场白插槽）：轮次数 × MINUTES_PER_QUESTION。"""
    return question_count * MINUTES_PER_QUESTION


def domain_label(domain: str) -> str:
    """域中文标签；项目深挖题（domain="project"）不在 DOMAIN_LABELS，单列。"""
    return DOMAIN_LABELS.get(domain) or (
        PROJECT_DOMAIN_LABEL if domain == PROJECT_DOMAIN else domain
    )


def _needs_space(left: str, right: str) -> bool:
    """中文排版：只在「汉字 ↔ 拉丁字母/数字」边界补空格（全角标点旁不补）。"""
    def han(ch: str) -> bool:
        return "一" <= ch <= "鿿"  # CJK 统一汉字区

    def latin(ch: str) -> bool:
        return ch.isascii() and ch.isalnum()

    return (han(left) and latin(right)) or (latin(left) and han(right))


def _with_label(text: str, label: str) -> str:
    """把 {label} 换成域标签，按中文排版在中文↔拉丁边界补空格。

    真实链路实测：不补空格会出现「看看RAG方面」「聊聊Memory。」挤在一起。标签自身可能
    是纯中文（「规划与推理范式」）也可能是中英混排（「Tool 与 Function Calling」），
    故空格取决于标签边界字符，而不是写死在模板里。
    """
    head, tail = text.split("{label}")
    left = " " if _needs_space(head[-1:], label[:1]) else ""
    right = " " if _needs_space(label[-1:], tail[:1]) else ""
    return f"{head}{left}{label}{right}{tail}"


def transition_kind(state: InterviewState, new_question: QuestionRecord) -> TransitionKind:
    """衔接分类（纯代码）：按「上一题（state.current_question）→ 新题」的题型/域判定。

    调用时机在 ask 节点换题前——此时 current_question 仍是上一题。
    """
    previous = state.current_question
    if previous is None:
        return TransitionKind.OPEN_PROJECT
    if previous.question_type == "scenario":
        return (
            TransitionKind.PROJECT_NEXT
            if new_question.question_type == "scenario"
            else TransitionKind.TO_TECH
        )
    if new_question.domain == previous.domain:
        return TransitionKind.SAME_DOMAIN
    return TransitionKind.SWITCH_DOMAIN


def transition_line(state: InterviewState, new_question: QuestionRecord) -> str:
    """题前衔接语（与题目拼成同一条消息）。变体按轮次轮换，确定性可回放。"""
    variants = _VARIANTS[transition_kind(state, new_question)]
    text = variants[state.answered_count % len(variants)]
    if "{label}" not in text:
        return text
    return _with_label(text, domain_label(new_question.domain))


def buffer_line(state: InterviewState) -> str | None:
    """答错缓冲（上一题有明确错误时单独成条）；无错误/首题返回 None。"""
    previous = state.current_question
    if previous is None or previous.score is None or not previous.score.error_flag:
        return None
    return _BUFFER_VARIANTS[state.answered_count % len(_BUFFER_VARIANTS)]


def reconnect_line(state: InterviewState) -> str | None:
    """重连问候（GET /interviews/{id}?reconnect=true）：重发当前题干作锚点。

    仅进行中且处于答题阶段（项目/技术）的场次生成；开场/自我介绍/反问阶段与已结束
    场次没有「刚才那道题」可回去，返回 None（前端静默恢复）。文案不落 checkpoint，
    只在响应里附一次——连续刷新不会堆叠。
    """
    if state.status != "running" or state.phase not in (Phase.PROJECT, Phase.TECH_BASE):
        return None
    question = state.current_question
    if question is None or not question.text:
        return None
    return (
        "欢迎回来，我们继续刚才的面试。"
        f"这道题是「{domain_label(question.domain)}」方向的：{question.text}"
    )
