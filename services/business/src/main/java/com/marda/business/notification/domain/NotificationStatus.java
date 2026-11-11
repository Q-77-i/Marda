package com.marda.business.notification.domain;

/** 通知状态（MVP 只写 pending，投递/重试是 J5 worker）。 */
public enum NotificationStatus {
    PENDING("pending"),
    SENT("sent"),
    FAILED("failed"),
    DEAD("dead");

    private final String apiValue;

    NotificationStatus(String apiValue) {
        this.apiValue = apiValue;
    }

    public String apiValue() {
        return apiValue;
    }
}
