package com.marda.business.notification.application;

import com.marda.business.event.NonRetryableEventException;
import com.marda.business.event.NotificationRequestedPayload;
import com.marda.business.inbox.ProcessedEventRepository;
import com.marda.business.notification.domain.Notification;
import com.marda.business.notification.domain.NotificationStatus;
import com.marda.business.notification.persistence.NotificationRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.util.UUID;

/**
 * 通知投递：去重 + 发送 + 状态迁移。
 *
 * <p>成功路径（inbox + sent）同一事务；失败路径在**独立事务**里记 failed / 退避，
 * 这样重试消息到来时仍能看到上一次的失败计数。
 */
@Service
public class NotificationDeliveryService {

    static final String CONSUMER = "notification-delivery";

    private final ProcessedEventRepository processed;
    private final NotificationRepository notifications;
    private final NotificationSender sender;
    private final Clock clock;
    private final long retryDelayMs;

    public NotificationDeliveryService(
            ProcessedEventRepository processed,
            NotificationRepository notifications,
            NotificationSender sender,
            Clock clock,
            @Value("${business.kafka.retry-delay-ms:1000}") long retryDelayMs) {
        this.processed = processed;
        this.notifications = notifications;
        this.sender = sender;
        this.clock = clock;
        this.retryDelayMs = retryDelayMs;
    }

    @Transactional
    public void deliver(UUID eventId, NotificationRequestedPayload payload) {
        if (!processed.markProcessed(eventId, CONSUMER, clock.instant())) {
            return;
        }
        UUID notificationId = parseId(payload.notificationId());
        Notification notification = notifications.findById(notificationId)
                .orElseThrow(() -> new NonRetryableEventException(
                        "通知行不存在：" + notificationId));
        if (notification.status() == NotificationStatus.SENT
                || notification.status() == NotificationStatus.DEAD) {
            return;
        }
        sender.send(notification);
        notifications.markSent(notificationId, clock.instant());
    }

    /** 失败记录用 REQUIRES_NEW：deliver 的事务会因异常回滚，这条必须留下。 */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void recordFailure(UUID notificationId) {
        notifications.markFailed(
                notificationId, clock.instant().plusMillis(retryDelayMs));
    }

    /** DLT handler：重试耗尽 → dead（对用户不可见，运维可查）。 */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void markDead(UUID notificationId) {
        notifications.markDead(notificationId);
    }

    private static UUID parseId(String value) {
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException exception) {
            throw new NonRetryableEventException("notification_id 不是 UUID", exception);
        }
    }
}
