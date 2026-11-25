package com.marda.business.event;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;
import org.springframework.stereotype.Component;

import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 事件信封解析 + 载荷校验。
 *
 * <p>任何失败都抛 {@link NonRetryableEventException}：重试改变不了「消息本身不合法」，
 * 只会在重试 topic 里空转 5 次。消费者用 {@code @RetryableTopic(exclude=...)} 直送 DLQ。
 */
@Component
public class EventEnvelopeReader {

    private final ObjectMapper objectMapper;
    private final Validator validator;

    public EventEnvelopeReader(ObjectMapper objectMapper, Validator validator) {
        this.objectMapper = objectMapper;
        this.validator = validator;
    }

    public <T> ParsedEvent<T> read(String message, String expectedType, Class<T> payloadType) {
        EventEnvelope envelope;
        try {
            envelope = objectMapper.readValue(message, EventEnvelope.class);
        } catch (Exception exception) {
            throw new NonRetryableEventException("事件不是合法 JSON 信封", exception);
        }
        if (envelope == null || envelope.type() == null) {
            throw new NonRetryableEventException("事件缺少 type");
        }
        if (!expectedType.equals(envelope.type())) {
            throw new NonRetryableEventException(
                    "事件 type 不符：期望 " + expectedType + "，实际 " + envelope.type());
        }
        if (envelope.version() != 1) {
            throw new NonRetryableEventException("不支持的事件版本：" + envelope.version());
        }
        if (envelope.payload() == null) {
            throw new NonRetryableEventException("事件缺少 payload");
        }
        UUID eventId;
        try {
            eventId = UUID.fromString(envelope.eventId());
        } catch (Exception exception) {
            throw new NonRetryableEventException("event_id 不是 UUID", exception);
        }

        T payload;
        try {
            payload = objectMapper.treeToValue(envelope.payload(), payloadType);
        } catch (Exception exception) {
            throw new NonRetryableEventException("payload 结构与契约不符", exception);
        }
        Set<ConstraintViolation<T>> violations = validator.validate(payload);
        if (!violations.isEmpty()) {
            String detail = violations.stream()
                    .map(violation -> violation.getPropertyPath() + " " + violation.getMessage())
                    .sorted()
                    .collect(Collectors.joining("; "));
            throw new NonRetryableEventException("payload 校验失败：" + detail);
        }
        return new ParsedEvent<>(eventId, payload);
    }
}
