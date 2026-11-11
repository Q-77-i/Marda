package com.marda.business.notification.domain;

/** 通知渠道（J3 仅 in_app；email / webhook 留 J5/J6）。 */
public enum NotificationChannel {
    IN_APP("in_app");

    private final String apiValue;

    NotificationChannel(String apiValue) {
        this.apiValue = apiValue;
    }

    public String apiValue() {
        return apiValue;
    }

    public static NotificationChannel fromApi(String value) {
        for (NotificationChannel channel : values()) {
            if (channel.apiValue.equals(value)) {
                return channel;
            }
        }
        throw new IllegalArgumentException("未知通知渠道：" + value);
    }
}
