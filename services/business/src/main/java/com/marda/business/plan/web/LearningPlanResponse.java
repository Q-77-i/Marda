package com.marda.business.plan.web;

import com.marda.business.plan.application.PlanView;
import com.marda.business.plan.domain.LearningPlan;

import java.time.Instant;
import java.util.List;

public record LearningPlanResponse(
        String id,
        String userId,
        String sourceInterviewId,
        String goal,
        String status,
        long version,
        Instant createdAt,
        Instant updatedAt,
        List<LearningTaskResponse> tasks) {

    public static LearningPlanResponse from(PlanView view) {
        LearningPlan plan = view.plan();
        return new LearningPlanResponse(
                plan.id().toString(),
                plan.userId(),
                plan.sourceInterviewId(),
                plan.goal(),
                plan.status().apiValue(),
                plan.version(),
                plan.createdAt(),
                plan.updatedAt(),
                view.tasks().stream().map(LearningTaskResponse::from).toList());
    }
}
