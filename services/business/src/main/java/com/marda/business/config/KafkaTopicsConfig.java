package com.marda.business.config;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/**
 * worker 角色显式建主 topic + DLQ（dev 1 分区 / RF=1；HA 由 env 调成 6 / 3）。
 *
 * <p>retry 档位由 {@code @RetryableTopic} 的退避自动创建：
 * {@code <event>.retry-1000/2000/4000/8000}；不在这里预建，避免与退避配置脱节。
 * DLQ 名字必须与 {@code dltTopicSuffix=".dlq"} 一致。
 */
@Configuration
@ConditionalOnProperty(name = "business.worker.enabled", havingValue = "true")
public class KafkaTopicsConfig {

    private final int partitions;
    private final int replicationFactor;

    public KafkaTopicsConfig(
            @Value("${business.kafka.partitions:1}") int partitions,
            @Value("${business.kafka.replication-factor:1}") int replicationFactor) {
        this.partitions = partitions;
        this.replicationFactor = replicationFactor;
    }

    private NewTopic topic(String name) {
        return TopicBuilder.name(name)
                .partitions(partitions)
                .replicas(replicationFactor)
                .build();
    }

    @Bean
    NewTopic interviewFinalizedTopic() {
        return topic("interview.finalized");
    }

    @Bean
    NewTopic interviewFinalizedDlqTopic() {
        return topic("interview.finalized.dlq");
    }

    @Bean
    NewTopic notificationRequestedTopic() {
        return topic("notification.requested");
    }

    @Bean
    NewTopic notificationRequestedDlqTopic() {
        return topic("notification.requested.dlq");
    }
}
