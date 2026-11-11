package com.marda.business.plan.application;

import com.marda.business.plan.domain.LearningTask;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** 内部 next 视图；无计划时 planId / generatedAt 为 null。 */
public record NextView(UUID planId, List<LearningTask> tasks, Instant generatedAt) {
}
