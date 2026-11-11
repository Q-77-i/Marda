package com.marda.business.plan.domain;

import java.time.Instant;
import java.util.UUID;

/** 已落库的学习任务。 */
public record LearningTask(
        UUID id,
        UUID planId,
        String domain,
        String title,
        String resourceUrl,
        TaskStatus status,
        Instant dueAt,
        int priority,
        long version,
        Instant createdAt,
        Instant updatedAt) {
}
