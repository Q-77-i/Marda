package com.marda.business.outbox;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.marda.business.event.EventEnvelope;
import com.marda.business.event.NotificationRequestedPayload;
import com.marda.business.notification.domain.Notification;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * 事件工厂：确定性 event_id（uuid v3）。
 *
 * <p>同一通知重放算出同一个 event_id，配合 outbox 唯一键与消费者 processed_event，
 * 至少一次投递不会变成重复业务。
 */
@Component
public class OutboxEventFactory {

    private final ObjectMapper objectMapper;
    private final Clock clock;

    public OutboxEventFactory(ObjectMapper objectMapper, Clock clock) {
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    public EventEnvelope notificationRequested(Notification notification) {
        UUID eventId = UUID.nameUUIDFromBytes(
                ("notification.requested:" + notification.id())
                        .getBytes(StandardCharsets.UTF_8));
        NotificationRequestedPayload payload = new NotificationRequestedPayload(
                notification.id().toString(),
                notification.userId(),
                notification.channel().apiValue(),
                notification.template(),
                notification.idempotencyKey());
        return new EventEnvelope(
                eventId.toString(),
                "notification.requested",
                1,
                OffsetDateTime.now(clock),
                null,
                "business",
                objectMapper.valueToTree(payload));
    }
}
