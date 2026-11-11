package com.marda.business.plan.domain;

import java.time.Instant;
import java.util.UUID;

/** 跳过建计划的审计行：空短板 / 行为面。 */
public record LearningPlanSkip(
        UUID id,
        String userId,
        String sourceInterviewId,
        SkipReason reason,
        Instant createdAt) {
}
