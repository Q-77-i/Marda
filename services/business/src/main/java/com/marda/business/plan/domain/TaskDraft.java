package com.marda.business.plan.domain;

import java.time.Instant;

/** 计划工厂产出的任务草稿（尚未分配 id / version）。 */
public record TaskDraft(String domain, String title, Instant dueAt, int priority) {
}
