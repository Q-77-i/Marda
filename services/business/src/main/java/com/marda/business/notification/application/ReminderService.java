package com.marda.business.notification.application;

import com.marda.business.notification.domain.Notification;
import com.marda.business.notification.domain.NotificationChannel;
import com.marda.business.notification.domain.NotificationStatus;
import com.marda.business.notification.persistence.NotificationRepository;
import com.marda.business.outbox.BusinessOutboxRepository;
import com.marda.business.outbox.OutboxEventFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.util.UUID;

/**
 * 提醒服务：写 in_app 通知行 + business_outbox(notification.requested)，同一本地事务。
 *
 * <p>行先落库、Kafka 只负责「投递」：Kafka 挂时通知行仍在（pending），outbox 积压，
 * 恢复后 worker 投递并标 sent。J3 的 202 语义不变。
 */
@Service
public class ReminderService {

    private static final String TEMPLATE = "reminder_test";
    private static final String PAYLOAD = "{\"message\":\"这是一条测试提醒\"}";

    private final NotificationRepository notifications;
    private final BusinessOutboxRepository outbox;
    private final OutboxEventFactory events;
    private final Clock clock;

    public ReminderService(
            NotificationRepository notifications,
            BusinessOutboxRepository outbox,
            OutboxEventFactory events,
            Clock clock) {
        this.notifications = notifications;
        this.outbox = outbox;
        this.events = events;
        this.clock = clock;
    }

    @Transactional
    public Notification testReminder(String userId) {
        Instant now = clock.instant();
        UUID notificationId = UUID.randomUUID();
        Notification notification = new Notification(
                notificationId,
                userId,
                NotificationChannel.IN_APP,
                TEMPLATE,
                PAYLOAD,
                NotificationStatus.PENDING,
                0,
                null,
                "reminder_test:" + notificationId,
                now,
                null);
        notifications.insert(notification);
        outbox.insert(
                events.notificationRequested(notification),
                "notification",
                notificationId.toString(),
                userId,
                now);
        return notification;
    }
}
