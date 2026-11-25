package com.marda.business.plan.domain;

/** 任务状态（与契约的 `status` 枚举一致）。 */
public enum TaskStatus {
    TODO("todo"),
    IN_PROGRESS("in_progress"),
    DONE("done"),
    SKIPPED("skipped");

    private final String apiValue;

    TaskStatus(String apiValue) {
        this.apiValue = apiValue;
    }

    public String apiValue() {
        return apiValue;
    }

    public boolean terminal() {
        return this == DONE || this == SKIPPED;
    }

    public static TaskStatus fromApi(String value) {
        for (TaskStatus status : values()) {
            if (status.apiValue.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("未知任务状态：" + value);
    }
}
