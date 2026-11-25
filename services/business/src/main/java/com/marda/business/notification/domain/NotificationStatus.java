package com.marda.business.notification.domain;

/** 通知状态：pending → sent / failed；failed → pending（重试）/ dead（DLQ）。 */
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

    public static NotificationStatus fromApi(String value) {
        for (NotificationStatus status : values()) {
            if (status.apiValue.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("未知通知状态：" + value);
    }
}
