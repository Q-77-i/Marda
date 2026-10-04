"""降级语义（P2-M9，SPEC §3/§11）：断 LLM 时各节点确定性兜底的原因与标记出口。

一条纪律：**降级 = 照常走完 + 如实标注**——不走 SSE error、不中断面试；但也不许静默：
状态里累计原因（进报告 payload，报告页/PDF/能力档案据此如实交代），流里发一条
`degraded` 事件（前端实时提示）。`mark` 把这两件事绑在一个调用里，避免「标了状态没提示」
或反之——两处只写一处，就是一次静默降级。

原因用**中文一句话**而不是机器码：没有任何消费者按单条原因分支（消费方只区分「是否降级」），
中文直接展示给用户，省掉前后端两份映射表（domain/question_type 那套映射是因为有分支语义）。
"""

from __future__ import annotations

from app import llm
from app.graph.rules import stream
from app.graph.state import InterviewState

# 原因文案（唯一来源：状态、报告 payload、PDF 都消费它）
SCRIPT_FALLBACK = "AI 文案服务不可用，已改用固定文案"
QUESTION_FALLBACK = "出题服务不可用，已改用预置兜底题"
UNSCORED = "评分服务不可用，未评分的题目不计入能力评估"
REPORT_MODEL_FALLBACK = "报告模型（v4-pro）不可用，已降级为标准模型生成"
REPORT_FALLBACK = "报告文字服务不可用，已输出确定性内容（分数与逐题记录不受影响）"
PROFILE_SKIPPED = "自我介绍提炼服务不可用，已跳过（不影响出题与评分）"


def mark(state: InterviewState, reason: str) -> None:
    """记一次降级：状态去重累计 + 发一条 SSE 提示（两件事成对且同步去重）。

    同一种降级只提示一次（五道题都未评分 = 一个原因、一条提示），状态里也只留一条。
    """
    if reason in state.degraded_reasons:
        return
    state.degraded_reasons.append(reason)
    stream.degraded(reason)


def reraise_if_content(exc: llm.LLMError) -> None:
    """**降级的判据 = 「重试治不好」**：只有上游不可用类失败才降级，其余原样抛回。

    - `retryable=True`（连接/超时/429/5xx/熔断/排队超时）→ 返回，调用方走兜底；
    - `retryable=False`（JSON 校验失败、空输出、400/401 配置错）→ **抛回上层**，
      保持既有 SSE error + 用户重试语义（P1-M4.7：重试 = 重跑失败节点）。

    为什么必须分：DeepSeek 偶发非法 JSON 是本项目已知抖动，重试即好——降级会把它
    静默吞成「未评分 / 兜底文案」，用户看到的是质量下降而不是一次可重试的错误。
    """
    if not exc.retryable:
        raise exc
