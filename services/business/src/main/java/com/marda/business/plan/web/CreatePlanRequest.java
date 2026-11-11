package com.marda.business.plan.web;

import com.marda.business.plan.domain.PlanInput;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** 公开建计划请求：J3 由浏览器从报告 payload 映射摘要；J5 事件路径走内部端点。 */
public record CreatePlanRequest(
        @NotBlank @Size(max = 128) String sourceInterviewId,
        @Size(max = 200) String goal,
        @Size(max = 64) String position,
        @Pattern(regexp = "tech|behavioral") String interviewType,
        @NotNull @Valid ReportSummaryRequest reportSummary) {

    public PlanInput toPlanInput(String userId) {
        return new PlanInput(
                userId,
                sourceInterviewId,
                goal,
                position,
                interviewType,
                reportSummary.toDomain());
    }
}
