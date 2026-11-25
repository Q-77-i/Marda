package com.marda.business.outbox;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * outbox relay：worker 角色里的定时发布器。
 *
 * <p>只做「领取 → 发布 → 标记」，不碰通知内容；通知状态由 notification worker 负责。
 * 领取用租约：relay 崩溃后行会在租约到期时被重新领取（至少一次）。
 */
@Component
@ConditionalOnProperty(name = "business.worker.enabled", havingValue = "true")
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final BusinessOutboxRepository outbox;
    private final EventPublisher publisher;
    private final BusinessOutboxProperties properties;
    private final Clock clock;
    private volatile long lastCleanupEpochDay = Long.MIN_VALUE;

    public OutboxRelay(
            BusinessOutboxRepository outbox,
            EventPublisher publisher,
            BusinessOutboxProperties properties,
            Clock clock) {
        this.outbox = outbox;
        this.publisher = publisher;
        this.properties = properties;
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${business.outbox.poll-interval-ms:1000}")
    public void poll() {
        try {
            Instant now = clock.instant();
            List<OutboxRow> rows = outbox.claim(
                    properties.batchSize(), properties.leaseSeconds(), now);
            if (rows.isEmpty()) {
                cleanupIfDue(now);
                return;
            }

            Map<UUID, String> outcomes = publisher.publishBatch(rows);
            int published = 0;
            int failed = 0;
            for (OutboxRow row : rows) {
                String error = outcomes.containsKey(row.id())
                        ? outcomes.get(row.id())
                        : "publisher did not report delivery";
                if (error == null) {
                    outbox.markPublished(List.of(row.id()), now);
                    published++;
                } else {
                    outbox.markFailed(
                            row.id(), error, now.plusSeconds(backoffSeconds(row.attempts() + 1)));
                    failed++;
                }
            }
            log.info("outbox relay claimed={} published={} failed={} pending={}",
                    rows.size(), published, failed, outbox.countPending());
            cleanupIfDue(now);
        } catch (Exception exception) {
            // 不让一次 DB / Kafka 抖动把定时任务打死；下一轮继续。
            log.warn("outbox relay 轮询失败：{}", exception.getMessage());
        }
    }

    long backoffSeconds(int attempts) {
        int exponent = Math.min(30, Math.max(0, attempts - 1));
        long delay = properties.baseBackoffSeconds() * (1L << exponent);
        return Math.min(properties.maxBackoffSeconds(), delay);
    }

    private void cleanupIfDue(Instant now) {
        long today = now.atZone(ZoneOffset.UTC).toLocalDate().toEpochDay();
        if (today == lastCleanupEpochDay) {
            return;
        }
        int removed = outbox.cleanup(properties.retentionDays(), now);
        lastCleanupEpochDay = today;
        if (removed > 0) {
            log.info("outbox 清理 published 行：{}", removed);
        }
    }
}
