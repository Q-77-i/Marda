package com.marda.business.plan.application;

import java.time.Instant;

/** 任务更新的应用层命令；dueAtPresent 区分“未提供”与“显式 null”。 */
public record TaskUpdateCommand(String status, boolean dueAtPresent, Instant dueAt, long version) {
}
