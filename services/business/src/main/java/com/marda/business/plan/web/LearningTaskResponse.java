package com.marda.business.plan.web;

import com.marda.business.plan.domain.LearningTask;

import java.time.Instant;

public record LearningTaskResponse(
        String id,
        String planId,
        String domain,
        String title,
        String resourceUrl,
        String status,
        Instant dueAt,
        int priority,
        long version) {

    public static LearningTaskResponse from(LearningTask task) {
        return new LearningTaskResponse(
                task.id().toString(),
                task.planId().toString(),
                task.domain(),
                task.title(),
                task.resourceUrl(),
                task.status().apiValue(),
                task.dueAt(),
                task.priority(),
                task.version());
    }
}
