package com.marda.business.plan.domain;

import java.util.List;

/** 报告摘要：建计划只需要总分与短板域，不需要题干 / 答案 / 关键点原文。 */
public record ReportSummary(double overall, List<WeakDomain> weakDomains) {
}
