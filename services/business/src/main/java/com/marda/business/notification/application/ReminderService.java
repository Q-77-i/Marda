package com.marda.business.notification.application;

import com.marda.business.notification.domain.Notification;
import com.marda.business.notification.domain.NotificationChannel;
import com.marda.business.notification.domain.NotificationStatus;
import com.marda.business.notification.persistence.NotificationRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.util.UUID;

/** 提醒服务：J3 只写 in_app pending 行；投递/重试在 J5。 */
@Service
public class ReminderService {

    private static final String TEMPLATE = "reminder_test";
    private static final String PAYLOAD = "{\"message\":\"这是一条测试提醒\"}";

    private final NotificationRepository notifications;
    private final Clock clock;

    public ReminderService(NotificationRepository notifications, Clock clock) {
        this.notifications = notifications;
        this.clock = clock;
    }

    @Transactional
    public Notification testReminder(String userId) {
        Instant now = clock.instant();
        Notification notification = new Notification(
                UUID.randomUUID(),
                userId,
                NotificationChannel.IN_APP,
                TEMPLATE,
                PAYLOAD,
                NotificationStatus.PENDING,
                0,
                null,
                null,
                now);
        notifications.insert(notification);
        return notification;
    }
}
