package com.marda.business.plan.domain;

/** 计划状态（与契约的 `status` 枚举一致）。 */
public enum PlanStatus {
    ACTIVE("active"),
    COMPLETED("completed"),
    ARCHIVED("archived");

    private final String apiValue;

    PlanStatus(String apiValue) {
        this.apiValue = apiValue;
    }

    public String apiValue() {
        return apiValue;
    }

    public static PlanStatus fromApi(String value) {
        for (PlanStatus status : values()) {
            if (status.apiValue.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("未知计划状态：" + value);
    }
}
