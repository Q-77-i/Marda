package com.marda.business.notification.domain;

import java.time.Instant;
import java.util.UUID;

/** 已落库的通知。J3 只写 pending；重试字段为 J5 预留。 */
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
        Instant createdAt) {
}
