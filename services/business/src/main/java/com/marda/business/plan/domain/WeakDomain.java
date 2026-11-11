package com.marda.business.plan.domain;

/** 报告里的一个短板域（事件 schema 的 `report_summary.weak_domains[]`）。 */
public record WeakDomain(String domain, double score, String advice) {
}
