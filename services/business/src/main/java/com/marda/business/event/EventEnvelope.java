package com.marda.business.event;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.JsonNode;

import java.time.OffsetDateTime;

/**
 * Kafka 事件统一信封（docs/p5/contracts/event-envelope.schema.json）。
 *
 * <p>{@code @JsonInclude(NON_NULL)} 是必需的：application.yml 全局是
 * {@code default-property-inclusion: always}，会把可选的 {@code trace_id: null}
 * 也写出来，而契约里它是 32 位 hex 字符串 —— null 会让消费者 schema 校验失败。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record EventEnvelope(
        String eventId,
        String type,
        int version,
        OffsetDateTime occurredAt,
        String traceId,
        String producer,
        JsonNode payload) {
}
