package com.marda.business.plan.application;

import com.marda.business.plan.domain.SkipReason;

/** 建计划结果：新建 / 幂等重放 / 跳过。 */
public record CreatePlanResult(Outcome outcome, PlanView view, SkipReason skipReason) {

    public enum Outcome {
        CREATED,
        EXISTING,
        SKIPPED
    }

    public static CreatePlanResult created(PlanView view) {
        return new CreatePlanResult(Outcome.CREATED, view, null);
    }

    public static CreatePlanResult existing(PlanView view) {
        return new CreatePlanResult(Outcome.EXISTING, view, null);
    }

    public static CreatePlanResult skipped(SkipReason reason) {
        return new CreatePlanResult(Outcome.SKIPPED, null, reason);
    }
}
