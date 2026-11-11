package com.marda.business.plan.domain;

/** 不建计划的原因；写入 `learning_plan_skip` 审计。 */
public enum SkipReason {
    NO_WEAK_DOMAINS,
    UNSUPPORTED_INTERVIEW_TYPE
}
