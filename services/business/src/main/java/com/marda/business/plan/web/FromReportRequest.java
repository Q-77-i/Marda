package com.marda.business.plan.web;

import com.marda.business.plan.domain.PlanInput;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.OffsetDateTime;

/** 内部 from-report 请求：`interview.finalized` payload 的 JSON 形状。 */
public record FromReportRequest(
        @NotBlank String interviewId,
        @NotBlank String userId,
        @NotBlank @Size(max = 64) String position,
        @NotBlank @Pattern(regexp = "tech|behavioral") String interviewType,
        @NotNull OffsetDateTime finishedAt,
        @NotBlank String reportId,
        @NotNull @Valid ReportSummaryRequest reportSummary) {

    public PlanInput toPlanInput() {
        return new PlanInput(
                userId,
                interviewId,
                null,
                position,
                interviewType,
                reportSummary.toDomain());
    }
}
