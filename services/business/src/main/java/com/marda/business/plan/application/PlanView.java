package com.marda.business.plan.application;

import com.marda.business.plan.domain.LearningPlan;
import com.marda.business.plan.domain.LearningTask;

import java.util.List;

/** 计划 + 任务的应用层视图。 */
public record PlanView(LearningPlan plan, List<LearningTask> tasks) {
}
