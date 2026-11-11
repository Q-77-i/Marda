package com.marda.business.plan.domain;

/** 计划工厂的决策：建计划或跳过（跳过也要留审计，重复事件不重试）。 */
public sealed interface PlanDecision permits PlanDecision.Create, PlanDecision.Skip {

    record Create(PlanDraft draft) implements PlanDecision {
    }

    record Skip(SkipReason reason) implements PlanDecision {
    }
}
