package com.marda.business.outbox;

import java.time.Instant;
import java.util.UUID;

/** 已领取的一条 outbox 行；payload 是完整事件信封 JSON。 */
public record OutboxRow(
        UUID id,
        String eventType,
        String partitionKey,
        String payload,
        int attempts,
        Instant createdAt,
        Instant nextAttemptAt) {
}
