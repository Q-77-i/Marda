package com.marda.business.config;

import com.marda.business.outbox.BusinessOutboxProperties;
import com.marda.business.outbox.EventPublisher;
import com.marda.business.outbox.KafkaEventPublisher;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.StringSerializer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;

import java.util.HashMap;
import java.util.Map;

/**
 * worker 角色的 Kafka 生产者。
 *
 * <p>只在 {@code business.worker.enabled=true} 时装配：web 角色不建生产者，也不连 Kafka。
 * 面试 / 业务 API 的可用性不依赖这里。
 */
@Configuration
@ConditionalOnProperty(name = "business.worker.enabled", havingValue = "true")
public class WorkerConfig {

    @Bean
    public ProducerFactory<String, String> eventProducerFactory(
            @Value("${spring.kafka.bootstrap-servers:localhost:9094}") String bootstrapServers) {
        Map<String, Object> config = new HashMap<>();
        config.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        config.put(ProducerConfig.ACKS_CONFIG, "all");
        config.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        config.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "zstd");
        config.put(ProducerConfig.LINGER_MS_CONFIG, 5);
        config.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        config.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        return new DefaultKafkaProducerFactory<>(config);
    }

    @Bean
    public KafkaTemplate<String, String> eventKafkaTemplate(
            ProducerFactory<String, String> eventProducerFactory) {
        return new KafkaTemplate<>(eventProducerFactory);
    }

    @Bean
    public EventPublisher eventPublisher(
            KafkaTemplate<String, String> eventKafkaTemplate,
            BusinessOutboxProperties properties) {
        return new KafkaEventPublisher(eventKafkaTemplate, properties);
    }
}
