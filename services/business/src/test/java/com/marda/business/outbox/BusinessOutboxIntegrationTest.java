package com.marda.business.outbox;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.marda.business.event.EventEnvelope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * J5 outbox 的公开行为：同事务入队、租约领取、发布标记、失败退避、relay 编排。
 *
 * <p>不测 Kafka 客户端本身；Kafka 真发布在 T5 的 Testcontainers Kafka 用例里。
 */
@SpringBootTest
@Testcontainers
class BusinessOutboxIntegrationTest {

    private static final Instant NOW = Instant.parse("2026-11-20T12:00:00Z");

    @Container
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>("postgres:16-alpine")
                    .withDatabaseName("marda_business")
                    .withUsername("marda")
                    .withPassword("marda");

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("business.service-token", () -> "test-service-token");
    }

    @Autowired
    BusinessOutboxRepository outbox;

    @Autowired
    JdbcClient jdbc;

    @Autowired
    ObjectMapper objectMapper;

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE business_outbox, notification, processed_event CASCADE").update();
    }

    private EventEnvelope event(String notificationId) {
        return new EventEnvelope(
                UUID.nameUUIDFromBytes(("notification.requested:" + notificationId)
                        .getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString(),
                "notification.requested",
                1,
                OffsetDateTime.ofInstant(NOW, ZoneOffset.UTC),
                null,
                "business",
                objectMapper.valueToTree(Map.of(
                        "notification_id", notificationId,
                        "user_id", "u1",
                        "channel", "in_app",
                        "template", "plan_created")));
    }

    private static EventPublisher publisher(List<OutboxRow> seen, Map<UUID, String> override) {
        return rows -> {
            seen.addAll(rows);
            Map<UUID, String> outcomes = new HashMap<>();
            for (OutboxRow row : rows) {
                outcomes.put(row.id(), override.get(row.id()));
            }
            return outcomes;
        };
    }

    @Test
    void insert_is_idempotent_by_aggregate_and_event_type() {
        assertThat(outbox.insert(event("n-1"), "notification", "n-1", "u1", NOW)).isTrue();
        // 同一聚合 + 事件类型、不同 event_id：唯一键挡住。
        EventEnvelope another = new EventEnvelope(
                UUID.randomUUID().toString(), "notification.requested", 1,
                OffsetDateTime.ofInstant(NOW, ZoneOffset.UTC), null, "business",
                objectMapper.valueToTree(Map.of("notification_id", "n-1")));
        assertThat(outbox.insert(another, "notification", "n-1", "u1", NOW)).isFalse();
        assertThat(outbox.countPending()).isEqualTo(1);
    }

    @Test
    void relay_publishes_and_marks_published() throws Exception {
        assertThat(outbox.insert(event("n-1"), "notification", "n-1", "u1", NOW)).isTrue();
        List<OutboxRow> seen = new ArrayList<>();
        OutboxRelay relay = new OutboxRelay(
                outbox,
                publisher(seen, Map.of()),
                BusinessOutboxProperties.defaults(),
                Clock.fixed(NOW, ZoneOffset.UTC));

        relay.poll();

        assertThat(seen).hasSize(1);
        // JSONB 的 ::text 会带空格（PG 规范化），断言解析后的字段而不是原始字符串
        assertThat(objectMapper.readTree(seen.get(0).payload()).get("type").asText())
                .isEqualTo("notification.requested");
        assertThat(outbox.countPending()).isZero();
        assertThat(jdbc.sql("SELECT count(*) FROM business_outbox WHERE published_at IS NOT NULL")
                .query(Integer.class).single()).isEqualTo(1);
    }

    @Test
    void relay_marks_failed_row_with_backoff_and_does_not_republish_immediately() {
        assertThat(outbox.insert(event("n-1"), "notification", "n-1", "u1", NOW)).isTrue();
        List<OutboxRow> seen = new ArrayList<>();
        OutboxRelay relay = new OutboxRelay(
                outbox,
                rows -> {
                    seen.addAll(rows);
                    Map<UUID, String> outcomes = new HashMap<>();
                    for (OutboxRow row : rows) {
                        outcomes.put(row.id(), "kafka down");
                    }
                    return outcomes;
                },
                BusinessOutboxProperties.defaults(),
                Clock.fixed(NOW, ZoneOffset.UTC));

        relay.poll();

        assertThat(seen).hasSize(1);
        assertThat(outbox.countPending()).isEqualTo(1);
        assertThat(jdbc.sql("SELECT attempts, last_error FROM business_outbox")
                .query((rs, rowNum) -> Map.of(
                        "attempts", rs.getInt("attempts"),
                        "error", rs.getString("last_error")))
                .single())
                .containsEntry("attempts", 1)
                .containsEntry("error", "kafka down");
        // 失败行进入退避：同一时刻再领不到。
        assertThat(outbox.claim(10, 60, NOW)).isEmpty();
    }

    @Test
    void claim_is_exclusive_until_lease_expires() {
        assertThat(outbox.insert(event("n-1"), "notification", "n-1", "u1", NOW)).isTrue();

        List<OutboxRow> first = outbox.claim(10, 60, NOW);
        List<OutboxRow> second = outbox.claim(10, 60, NOW);

        assertThat(first).hasSize(1);
        assertThat(second).isEmpty();
        // 租约到期后可再次领取（模拟 relay 崩溃）。
        assertThat(outbox.claim(10, 60, NOW.plusSeconds(61))).hasSize(1);
    }
}
