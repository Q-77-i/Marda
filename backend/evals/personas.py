"""三档候选人 persona（P1-M12 会话 2）：为「同一道题 × 弱/中/强」生成对照回答。

**为什么归档在这里**：弱/中两档 persona 最早写在一次性驱动脚本里
（M9/M10 真机实测用过，`/tmp` 随重启即丢）。它是我自己写的模板，不是题库内容，
可以进仓库；放在 `evals/` 而非 `scripts/`，因为它是被脚本 import 的库模块。

**实测定标（M9/M10 两轮真机）**：弱档 persona 实得 1.40–1.96、中档 3.60–3.74——
persona 指令与评分官的映射不是线性的（「像样的回答」容易被给到 3.5+），
故**不预设档位分数**：三档只保证回答形态递减，期望分由独立的标注流程给（见
`scripts/eval_judge_golden.py`），单调性由指标检验，不靠 persona 自证。

**硬约束**：persona 看不到 key_points（看得到就会照着答，覆盖率与分数失去区分度），
由单测钉死。
"""

from __future__ import annotations

from app import llm

TIERS: tuple[str, ...] = ("weak", "medium", "strong")

PERSONA_TEMPLATES: dict[str, str] = {
    "weak": (
        "你是一名准备不充分的校招候选人，技术水平明显偏弱。"
        "回答必须简短（2–3 句）：只提一两个相关名词或方向，说不清其中的原理与机制，"
        "大部分要点答不到；不会的就凭印象说个大概，不要编造细节、不要反问我。"
    ),
    "medium": (
        "你是一名中等水平的校招候选人。"
        "回答控制在 3–4 句：能说出主要方向和一两个要点，但缺少底层机制、边界条件与权衡分析，"
        "偶尔有小的不准确；不要反问我。"
    ),
    "strong": (
        "你是一名准备充分、水平优秀的校招候选人（做过真实的 Agent 项目）。"
        "回答 5–8 句：先给结论，再讲清原理与机制，主动带出边界条件与方案权衡，"
        "并举一个自己项目里的实际做法；不要反问我。"
    ),
}

ANSWER_TEMPLATE = (
    "{persona}\n"
    "下面是面试官问你的题目，请直接作答——像现场口述：不要 Markdown 标题、"
    "不要分点编号、不要在回答里复述题目。\n"
    "【题目】{question}"
)


async def generate_tier_answer(question: str, tier: str, *, temperature: float = 0.7) -> str:
    """按档位生成一个回答（结果会**冻结进 golden**，故这里的随机性不影响可复现性）。

    温度沿用文案类默认（0.7）：三档要的是「同一个人设的稳定口吻」，不是多样性。
    """
    if tier not in PERSONA_TEMPLATES:
        raise ValueError(f"未知档位 {tier!r}（可选 {TIERS}）")
    return await llm.chat(
        [{"role": "system", "content": ANSWER_TEMPLATE.format(
            persona=PERSONA_TEMPLATES[tier], question=question)}],
        temperature=temperature,
    )
