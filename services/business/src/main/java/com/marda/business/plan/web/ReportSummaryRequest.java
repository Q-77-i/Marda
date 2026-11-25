package com.marda.business.plan.web;

import com.marda.business.plan.domain.ReportSummary;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.List;

/** 与事件 schema 的 `report_summary` 同构；不含题干 / 答案 / 关键点原文。 */
public record ReportSummaryRequest(
        @DecimalMin("0.0") @DecimalMax("5.0") double overall,
        @NotNull @Size(max = 5) List<@Valid WeakDomainRequest> weakDomains) {

    public ReportSummary toDomain() {
        return new ReportSummary(
                overall,
                weakDomains.stream().map(WeakDomainRequest::toDomain).toList());
    }
}
