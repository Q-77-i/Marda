package com.marda.business.plan.web;

import com.marda.business.plan.application.NextView;

import java.time.Instant;
import java.util.List;

public record NextPlanResponse(String planId, List<LearningTaskResponse> tasks, Instant generatedAt) {

    public static NextPlanResponse from(NextView view) {
        return new NextPlanResponse(
                view.planId() == null ? null : view.planId().toString(),
                view.tasks().stream().map(LearningTaskResponse::from).toList(),
                view.generatedAt());
    }
}
