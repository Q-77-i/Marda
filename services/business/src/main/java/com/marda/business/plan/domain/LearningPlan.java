package com.marda.business.plan.domain;

import java.time.Instant;
import java.util.UUID;

/** 已落库的学习计划。 */
public record LearningPlan(
        UUID id,
        String userId,
        String sourceInterviewId,
        String goal,
        PlanStatus status,
        long version,
        String idempotencyKey,
        String requestHash,
        Instant createdAt,
        Instant updatedAt) {
}
