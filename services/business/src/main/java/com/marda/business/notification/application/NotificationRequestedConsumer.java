package com.marda.business.notification.application;

import com.marda.business.event.EventEnvelopeReader;
import com.marda.business.event.NonRetryableEventException;
import com.marda.business.event.NotificationRequestedPayload;
import com.marda.business.event.ParsedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.DltHandler;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.annotation.RetryableTopic;
import org.springframework.kafka.retrytopic.SameIntervalTopicReuseStrategy;
import org.springframework.retry.annotation.Backoff;
import org.springframework.stereotype.Component;

import java.util.UUID;

/**
 * `notification.requested` 消费者：通知行 → 投递 → sent / failed / dead。
 *
 * <p>in_app 的发送是本地标记；失败（DB / future 外部渠道）走 retry topic，
 * 5 次后 DLT handler 把通知置 dead。
 */
@Component
@ConditionalOnProperty(name = "business.worker.enabled", havingValue = "true")
public class NotificationRequestedConsumer {

    private static final Logger log = LoggerFactory.getLogger(NotificationRequestedConsumer.class);

    private final EventEnvelopeReader reader;
    private final NotificationDeliveryService delivery;

    public NotificationRequestedConsumer(
            EventEnvelopeReader reader,
            NotificationDeliveryService delivery) {
        this.reader = reader;
        this.delivery = delivery;
    }

    @KafkaListener(
            topics = "notification.requested",
            groupId = "${business.kafka.notification-consumer-group:notification-delivery}")
    @RetryableTopic(
            attempts = "5",
            backoff = @Backoff(delay = 1000, multiplier = 2.0),
            retryTopicSuffix = ".retry",
            dltTopicSuffix = ".dlq",
            sameIntervalTopicReuseStrategy = SameIntervalTopicReuseStrategy.SINGLE_TOPIC,
            exclude = NonRetryableEventException.class,
            autoCreateTopics = "true")
    public void onMessage(String message) {
        ParsedEvent<NotificationRequestedPayload> event = reader.read(
                message, "notification.requested", NotificationRequestedPayload.class);
        try {
            delivery.deliver(event.eventId(), event.payload());
        } catch (NonRetryableEventException exception) {
            throw exception;
        } catch (Exception exception) {
            delivery.recordFailure(UUID.fromString(event.payload().notificationId()));
            throw exception;
        }
    }

    @DltHandler
    public void onDlt(String message) {
        try {
            ParsedEvent<NotificationRequestedPayload> event = reader.read(
                    message, "notification.requested", NotificationRequestedPayload.class);
            delivery.markDead(UUID.fromString(event.payload().notificationId()));
            log.error("notification.requested 进入 DLQ，通知已标 dead：{}",
                    event.payload().notificationId());
        } catch (Exception exception) {
            // DLT handler 自己不能把 DLT 消息再炸一遍；留日志人工处理。
            log.error("通知 DLQ 处理失败：{}；原始消息：{}", exception.getMessage(), message);
        }
    }
}
