package com.marda.business.plan.web;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonSetter;
import com.marda.business.plan.application.TaskUpdateCommand;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.time.OffsetDateTime;

/**
 * PATCH 任务请求。
 *
 * <p>`due_at` 需要区分“没传”和“显式 null”：自定义 setter 记录是否出现过，
 * 前者保持原截止日，后者清空。
 */
public class TaskUpdateRequest {

    @NotNull
    @Min(1)
    private Long version;

    @Pattern(regexp = "todo|in_progress|done|skipped")
    private String status;

    private OffsetDateTime dueAt;

    private boolean dueAtPresent;

    public Long getVersion() {
        return version;
    }

    public void setVersion(Long version) {
        this.version = version;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public OffsetDateTime getDueAt() {
        return dueAt;
    }

    @JsonSetter("due_at")
    public void setDueAt(OffsetDateTime dueAt) {
        this.dueAt = dueAt;
        this.dueAtPresent = true;
    }

    @JsonIgnore
    public boolean isDueAtPresent() {
        return dueAtPresent;
    }

    public TaskUpdateCommand toCommand() {
        return new TaskUpdateCommand(
                status,
                dueAtPresent,
                dueAt == null ? null : dueAt.toInstant(),
                version == null ? 0L : version);
    }
}
