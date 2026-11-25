package com.marda.business.notification.domain;

import java.time.Instant;
import java.util.UUID;

/** 已落库的通知。J3 只写 pending；J5 起由 notification worker 投递并写 sent_at。 */
public record Notification(
        UUID id,
        String userId,
        NotificationChannel channel,
        String template,
        String payloadJson,
        NotificationStatus status,
        int retryCount,
        Instant nextRetryAt,
        String idempotencyKey,
        Instant createdAt,
        Instant sentAt) {
}
