"""断 LLM 时的确定性兜底内容（P2-M9，SPEC §3 降级链的最后一环）。

两条口径：

- **文案兜底**：开场白/收尾/陈词都是装饰，模板里已有全部插槽（岗位/题量/时长/人称），
  不依赖任何模型输出；
- **题目兜底**：内置的通用题——**自写、不进题库、不引任何语料原文**（红线口径），
  带 key_points（模型中途恢复后，这些题照常能被评分与追问）。按阶段/会话类型选，
  已问过的跳过（按题干去重），题池轮尽后从头再来（宁可重复也不能因为文案服务断了
  把面试卡死）。

选「预置题」而不是「复用题库里某道题」：题库检索本身可能也是断的（同一场故障），
兜底链的最后一环必须不依赖任何外部服务。
"""

from __future__ import annotations

from app.domain import (
    BEHAVIORAL_DOMAIN,
    INTERVIEW_BEHAVIORAL,
    DOMAIN_LABELS,
    PROJECT_DOMAIN,
    QUESTION_TYPE_BEHAVIORAL,
)
from app.graph.rules.quota import pick_domain
from app.graph.state import InterviewState, Phase, QuestionRecord

# 固定文案的 {persona} 插槽由调用方用 prompts.persona_for(...) 填——人设只有那一个
# 来源（节点本来就拿着它），这里不复制一份。
INTRO_FALLBACK = (
    "{persona}\n你好，我是「码达 Marda」的 AI 面试官。现在开始一场针对「{position}」岗位的"
    "{kind}，共 {question_count} 轮问答，大约需要 {duration} 分钟。每轮之后我会根据你的回答"
    "选择追问或换题，最后生成能力评估报告。请先做 1 分钟左右的自我介绍，讲讲你的背景和"
    "做过的项目。"
)

CLOSING_INVITE_FALLBACK = (
    "{persona}\n{section}到这里就差不多了。你有什么想问我的吗？可以问岗位、团队或技术方向，"
    "我会尽量回答；没有的话我们就进入报告环节。"
)

ANSWER_CANDIDATE_FALLBACK = (
    "{persona}\n关于「{content}」，我结合常见的做法说一下：这类问题的关键通常在于把需求拆成"
    "可验证的小步、明确取舍的边界条件，再用可观测的方式验证结果。更具体的细节，建议在"
    "后续交流里结合你的实际场景展开。还有别的想了解的吗？"
)

REFUSE_END_FALLBACK = (
    "{persona}\n我们才进行到一半左右，现在结束的话报告的说服力会打折扣。"
    "要不我们把当前这道题聊完——{question}——再决定？"
)

CLOSING_REMARK_FALLBACK = (
    "{persona}\n今天的面试到这里就结束了，感谢你的时间。评估报告已经生成，"
    "你可以查看逐题复盘与针对性的学习建议。"
)

# 内置兜底题：text / topic / key_points（自写通用题，不引语料原文）
_TECH: dict[str, dict] = {
    "agent-architecture": {
        "text": "请描述一个你设计或深入了解过的 Agent 系统架构，说明它的核心组件与数据流。",
        "key_points": ["组件划分与职责边界", "数据/控制流走向", "关键取舍与失败处理"],
    },
    "rag": {
        "text": "在一个 RAG 系统里，检索质量不理想时你会从哪些环节排查与优化？",
        "key_points": ["分块与嵌入", "检索策略（稀疏/稠密/混合与重排）", "评测与迭代闭环"],
    },
    "planning-reasoning": {
        "text": "一个需要多步执行的任务，怎么让模型做好规划并保证执行过程可纠偏？",
        "key_points": ["任务分解与依赖关系", "执行中反馈与重规划", "失败重试与终止条件"],
    },
    "tool-use": {
        "text": "让模型调用外部工具时，你会怎么设计工具接口与错误处理？",
        "key_points": ["工具描述与参数校验", "超时/重试/幂等", "错误信息的可理解性"],
    },
    "memory": {
        "text": "多轮对话里，Agent 的记忆应该怎么组织？短期与长期记忆各自解决什么问题？",
        "key_points": ["上下文窗口内的短期记忆", "长期记忆的写入与召回", "遗忘与压缩策略"],
    },
    "engineering-observability": {
        "text": "线上 Agent 服务出问题时，你会靠哪些手段定位与止损？",
        "key_points": ["链路追踪与日志", "成本/token 监控", "降级与熔断策略"],
    },
}

_PROJECT: list[dict] = [
    {
        "text": "挑一个你做过的项目，讲讲它的整体架构和你负责的部分，以及一个你印象最深的技术难点。",
        "key_points": ["项目背景与目标", "个人职责与贡献", "难点的定位与解决过程"],
    },
    {
        "text": "如果让你把做过的项目重做一遍，你会在架构或工程上做哪些不一样的选择？为什么？",
        "key_points": ["对原方案的反思", "取舍依据", "可验证的改进方向"],
    },
    {
        "text": "讲讲你项目里的一次失败或踩坑经历：当时怎么发现、怎么定位、最后怎么解决的？",
        "key_points": ["问题的发现路径", "定位手段", "修复与防复发"],
    },
]

_BEHAVIORAL: list[dict] = [
    {
        "text": "请讲一个你与他人协作中产生分歧的例子：你的立场是什么、最后怎么达成一致的？",
        "key_points": ["情境与任务", "你的具体行动", "结果与复盘"],
    },
    {
        "text": "讲一次你在时间紧、资源有限的情况下完成任务的经历，你是怎么安排优先级的？",
        "key_points": ["约束条件", "优先级取舍", "结果与反思"],
    },
    {
        "text": "说一个你主动学习新东西并用到实际项目里的例子。",
        "key_points": ["学习动机与路径", "落地方式", "带来的实际变化"],
    },
]


def fallback_question(state: InterviewState) -> QuestionRecord:
    """按阶段/会话类型取内置兜底题；已问过的跳过（题干去重），轮尽后从头再来。"""
    asked = {q.text for q in state.answered_questions}
    if state.interview_type == INTERVIEW_BEHAVIORAL:
        return _pick(
            _BEHAVIORAL, asked, domain=BEHAVIORAL_DOMAIN, difficulty=state.difficulty,
            question_type=QUESTION_TYPE_BEHAVIORAL,
        ) or _record(
            _BEHAVIORAL[0], BEHAVIORAL_DOMAIN, state.difficulty,
            question_type=QUESTION_TYPE_BEHAVIORAL,
        )
    if state.phase is Phase.TECH_BASE:
        # 域与配额选定同源（pick_domain 是纯代码）：回放事件里的域才不撒谎
        domain = pick_domain(state)
        if domain in _TECH:
            picked = _pick([_TECH[domain]], asked, domain=domain, difficulty=state.difficulty)
            if picked is not None:
                return picked
        for name, item in _TECH.items():  # 该域题已问过 → 任取一道没问过的
            picked = _pick([item], asked, domain=name, difficulty=state.difficulty)
            if picked is not None:
                return picked
        return _record(_TECH["agent-architecture"], "agent-architecture", state.difficulty)
    # PROJECT 阶段与首题（WARMUP 之后）：项目深挖题（question_type=scenario 同生成路径）
    return _pick(
        _PROJECT, asked, domain=PROJECT_DOMAIN, difficulty=state.difficulty, question_type="scenario"
    ) or _record(_PROJECT[0], PROJECT_DOMAIN, state.difficulty, question_type="scenario")


def _pick(
    pool: list[dict],
    asked: set[str],
    *,
    domain: str,
    difficulty: str,
    question_type: str = "tech",
) -> QuestionRecord | None:
    for item in pool:
        if item["text"] not in asked:
            return _record(item, domain, difficulty, question_type=question_type)
    return None


def _record(
    item: dict, domain: str, difficulty: str, *, question_type: str = "tech"
) -> QuestionRecord:
    return QuestionRecord(
        text=item["text"],
        domain=domain,
        topic=DOMAIN_LABELS.get(domain, ""),
        difficulty=difficulty,
        key_points=list(item["key_points"]),
        from_bank=False,
        question_type=question_type,
    )
