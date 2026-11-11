package com.marda.business.notification.web;

import com.marda.business.notification.domain.Notification;

public record ReminderTestResponse(String notificationId, String status) {

    public static ReminderTestResponse from(Notification notification) {
        return new ReminderTestResponse(
                notification.id().toString(), notification.status().apiValue());
    }
}
