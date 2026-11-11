package com.marda.business.plan.domain;

/**
 * 建计划的输入：公开 POST 与内部 from-report 两条路径共用。
 *
 * <p>`interviewType` 缺省按技术面处理；行为面没有可学习的短板域，直接跳过。
 */
public record PlanInput(
        String userId,
        String sourceInterviewId,
        String goal,
        String position,
        String interviewType,
        ReportSummary reportSummary) {
}
