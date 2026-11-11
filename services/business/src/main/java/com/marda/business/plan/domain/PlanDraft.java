package com.marda.business.plan.domain;

import java.util.List;

/** 计划工厂的产出：goal 与任务清单；不含 id / 时间戳 / 版本。 */
public record PlanDraft(String goal, List<TaskDraft> tasks) {
}
