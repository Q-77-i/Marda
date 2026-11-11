package com.marda.business.plan.web;

import com.marda.business.plan.application.PageResult;
import com.marda.business.plan.application.PlanView;

import java.util.List;

public record PaginatedPlansResponse(
        List<LearningPlanResponse> items,
        long total,
        int page,
        int pageSize) {

    public static PaginatedPlansResponse from(PageResult<PlanView> result) {
        return new PaginatedPlansResponse(
                result.items().stream().map(LearningPlanResponse::from).toList(),
                result.total(),
                result.page(),
                result.pageSize());
    }
}
