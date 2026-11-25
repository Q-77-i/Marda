package com.marda.business.outbox;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** outbox relay 配置；所有值都有安全默认，缺省不会把 relay 变成空转或无限重试。 */
@ConfigurationProperties(prefix = "business.outbox")
public record BusinessOutboxProperties(
        String topic,
        int batchSize,
        int leaseSeconds,
        long pollIntervalMs,
        long baseBackoffSeconds,
        long maxBackoffSeconds,
        int retentionDays,
        long publishTimeoutSeconds) {

    public BusinessOutboxProperties {
        topic = (topic == null || topic.isBlank()) ? "notification.requested" : topic;
        batchSize = batchSize <= 0 ? 100 : batchSize;
        leaseSeconds = leaseSeconds <= 0 ? 60 : leaseSeconds;
        pollIntervalMs = pollIntervalMs <= 0 ? 1000 : pollIntervalMs;
        baseBackoffSeconds = baseBackoffSeconds <= 0 ? 1 : baseBackoffSeconds;
        maxBackoffSeconds = maxBackoffSeconds <= 0 ? 60 : maxBackoffSeconds;
        retentionDays = retentionDays <= 0 ? 30 : retentionDays;
        publishTimeoutSeconds = publishTimeoutSeconds <= 0 ? 10 : publishTimeoutSeconds;
    }

    /** 单测 / 手动装配用。 */
    public static BusinessOutboxProperties defaults() {
        return new BusinessOutboxProperties(
                "notification.requested", 100, 60, 1000, 1, 60, 30, 10);
    }
}
