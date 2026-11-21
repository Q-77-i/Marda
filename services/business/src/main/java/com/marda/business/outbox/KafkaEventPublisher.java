package com.marda.business.outbox;

import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/** Kafka 发布：acks=all + 幂等（producer 配置在 WorkerConfig）；一次等整批结果。 */
public class KafkaEventPublisher implements EventPublisher {

    private final KafkaTemplate<String, String> template;
    private final BusinessOutboxProperties properties;

    public KafkaEventPublisher(
            KafkaTemplate<String, String> template,
            BusinessOutboxProperties properties) {
        this.template = template;
        this.properties = properties;
    }

    @Override
    public Map<UUID, String> publishBatch(List<OutboxRow> rows) {
        Map<UUID, CompletableFuture<SendResult<String, String>>> futures = new LinkedHashMap<>();
        Map<UUID, String> outcomes = new LinkedHashMap<>();

        for (OutboxRow row : rows) {
            try {
                ProducerRecord<String, String> record = new ProducerRecord<>(
                        properties.topic(), null, row.partitionKey(), row.payload());
                record.headers().add(
                        "event_id", row.id().toString().getBytes(StandardCharsets.UTF_8));
                record.headers().add(
                        "content_type", "application/json".getBytes(StandardCharsets.UTF_8));
                futures.put(row.id(), template.send(record));
            } catch (Exception exception) {
                outcomes.put(row.id(), exception.getClass().getSimpleName() + ": "
                        + exception.getMessage());
            }
        }

        for (Map.Entry<UUID, CompletableFuture<SendResult<String, String>>> entry
                : futures.entrySet()) {
            try {
                entry.getValue().get(properties.publishTimeoutSeconds(), TimeUnit.SECONDS);
                outcomes.put(entry.getKey(), null);
            } catch (Exception exception) {
                Throwable cause = exception.getCause() == null ? exception : exception.getCause();
                outcomes.put(entry.getKey(),
                        cause.getClass().getSimpleName() + ": " + cause.getMessage());
            }
        }
        return outcomes;
    }
}
