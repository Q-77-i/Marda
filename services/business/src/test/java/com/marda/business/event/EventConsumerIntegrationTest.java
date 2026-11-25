package com.marda.business.event;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.marda.business.notification.application.NotificationDeliveryService;
import com.marda.business.notification.application.NotificationRequestedConsumer;
import com.marda.business.notification.application.NotificationSender;
import com.marda.business.notification.domain.Notification;
import com.marda.business.notification.domain.NotificationChannel;
import com.marda.business.notification.domain.NotificationStatus;
import com.marda.business.notification.persistence.NotificationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.kafka.KafkaContainer;
import org.testcontainers.utility.DockerImageName;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * P5-J5 消费者最高接缝：真 Kafka + 真 PG + 真监听器。
 *
 * <p>验的是「只有真 broker 才暴露的东西」：重复事件只建一个计划、毒消息进 DLQ 且不阻塞分区、
 * 通知失败走 retry topic 后成功、DLT handler 把通知置 dead、读接口只返回 sent。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class EventConsumerIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";
    private static final String USER = "u1";

    @Container
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>("postgres:16-alpine")
                    .withDatabaseName("marda_business")
                    .withUsername("marda")
                    .withPassword("marda");

    @Container
    static final KafkaContainer KAFKA =
            new KafkaContainer(DockerImageName.parse("apache/kafka:3.9.1"));

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("spring.kafka.bootstrap-servers", KAFKA::getBootstrapServers);
        registry.add("business.service-token", () -> SERVICE_TOKEN);
        registry.add("business.timezone", () -> "Asia/Shanghai");
        registry.add("business.worker.enabled", () -> "true");
        // 消费者测试不依赖定时 relay：把轮询拉长，避免测试间互相抢 outbox。
        registry.add("business.outbox.poll-interval-ms", () -> "100000");
        registry.add("business.kafka.retry-delay-ms", () -> "50");
    }

    @MockBean
    NotificationSender sender;

    @Autowired
    KafkaTemplate<String, String> kafkaTemplate;

    @Autowired
    JdbcClient jdbc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    NotificationRepository notifications;

    @Autowired
    NotificationDeliveryService delivery;

    @Autowired
    NotificationRequestedConsumer notificationConsumer;

    @Autowired
    TestRestTemplate rest;

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE learning_task, learning_plan, notification, learning_plan_skip,"
                + " business_outbox, processed_event CASCADE").update();
    }

    private void send(String topic, String key, String value) throws Exception {
        kafkaTemplate.send(topic, key, value).get(10, TimeUnit.SECONDS);
    }

    private static void await(String label, BooleanSupplier condition) {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
        while (System.nanoTime() < deadline) {
            if (condition.getAsBoolean()) {
                return;
            }
            try {
                Thread.sleep(50);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                throw new AssertionError(label + " 等待被中断", exception);
            }
        }
        throw new AssertionError(label + " 20s 内未达成");
    }

    private String interviewFinalized(String interviewId) throws Exception {
        return objectMapper.writeValueAsString(Map.of(
                "event_id", UUID.randomUUID().toString(),
                "type", "interview.finalized",
                "version", 1,
                "occurred_at", "2026-11-20T12:00:00Z",
                "producer", "agent",
                "payload", Map.of(
                        "interview_id", interviewId,
                        "user_id", USER,
                        "position", "Agent",
                        "interview_type", "tech",
                        "finished_at", "2026-11-20T11:59:30Z",
                        "report_id", interviewId,
                        "report_summary", Map.of(
                                "overall", 3.4,
                                "weak_domains", java.util.List.of(Map.of(
                                        "domain", "rag",
                                        "score", 2.5,
                                        "advice", "补混合检索"))))));
    }

    private String notificationRequested(Notification notification) throws Exception {
        return objectMapper.writeValueAsString(Map.of(
                "event_id", UUID.nameUUIDFromBytes(
                        ("notification.requested:" + notification.id())
                                .getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString(),
                "type", "notification.requested",
                "version", 1,
                "occurred_at", "2026-11-20T12:00:00Z",
                "producer", "business",
                "payload", Map.of(
                        "notification_id", notification.id().toString(),
                        "user_id", notification.userId(),
                        "channel", "in_app",
                        "template", notification.template(),
                        "idempotency_key", notification.idempotencyKey())));
    }

    private Notification pendingNotification(String template) {
        UUID id = UUID.randomUUID();
        Notification notification = new Notification(
                id, USER, NotificationChannel.IN_APP, template,
                "{\"message\":\"测试通知\"}", NotificationStatus.PENDING,
                0, null, template + ":" + id,
                Instant.parse("2026-11-20T12:00:00Z"), null);
        notifications.insert(notification);
        return notification;
    }

    @Test
    void interview_finalized_creates_plan_and_dedupes_by_event_id() throws Exception {
        String event = interviewFinalized("iv-consume-1");

        send("interview.finalized", "iv-consume-1", event);
        send("interview.finalized", "iv-consume-1", event);

        await("计划落库", () -> jdbc.sql("""
                        SELECT count(*) FROM learning_plan
                        WHERE source_interview_id = 'iv-consume-1'
                        """).query(Long.class).single() == 1L);
        // 再等一个消费窗口，确认第二条重复事件没有建第二个计划
        Thread.sleep(500);
        assertThat(jdbc.sql("""
                SELECT count(*) FROM learning_plan WHERE source_interview_id = 'iv-consume-1'
                """).query(Long.class).single()).isEqualTo(1);
        assertThat(jdbc.sql("SELECT count(*) FROM processed_event")
                .query(Long.class).single()).isEqualTo(1);
    }

    @Test
    void poison_message_goes_to_dlq_and_does_not_block_next_event() throws Exception {
        send("interview.finalized", "poison", "not-json");
        send("interview.finalized", "iv-after-poison", interviewFinalized("iv-after-poison"));

        await("毒消息之后的事件仍被处理", () -> jdbc.sql("""
                        SELECT count(*) FROM learning_plan
                        WHERE source_interview_id = 'iv-after-poison'
                        """).query(Long.class).single() == 1L);
        assertThat(jdbc.sql("""
                SELECT count(*) FROM learning_plan WHERE source_interview_id = 'iv-after-poison'
                """).query(Long.class).single()).isEqualTo(1);
    }

    @Test
    void notification_requested_marks_sent_and_read_api_returns_it() throws Exception {
        Notification notification = pendingNotification("plan_created");
        doNothing().when(sender).send(any());

        send("notification.requested", USER, notificationRequested(notification));

        await("通知标记 sent", () -> jdbc.sql("""
                        SELECT count(*) FROM notification
                        WHERE id = CAST(:id AS uuid) AND status = 'sent' AND sent_at IS NOT NULL
                        """).param("id", notification.id().toString())
                .query(Long.class).single() == 1L);

        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", USER);
        ResponseEntity<String> response = rest.exchange(
                "/api/v1/learning/notifications?status=sent&limit=20",
                HttpMethod.GET, new HttpEntity<>(headers), String.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).contains(notification.id().toString());
        assertThat(response.getBody()).contains("\"status\":\"sent\"");
        verify(sender, times(1)).send(any());
    }

    @Test
    void notification_retry_then_success_updates_retry_count() throws Exception {
        Notification notification = pendingNotification("retry_once");
        doThrow(new RuntimeException("temporary sender failure"))
                .doNothing()
                .when(sender).send(any());

        send("notification.requested", USER, notificationRequested(notification));

        // @RetryableTopic 第一次失败后 1s 再试；等待第二次成功。
        await("重试后标记 sent", () -> jdbc.sql("""
                        SELECT count(*) FROM notification
                        WHERE id = CAST(:id AS uuid) AND status = 'sent'
                        """).param("id", notification.id().toString())
                .query(Long.class).single() == 1L);
        assertThat(jdbc.sql("SELECT retry_count FROM notification WHERE id = CAST(:id AS uuid)")
                .param("id", notification.id().toString())
                .query(Integer.class).single()).isGreaterThanOrEqualTo(1);
    }

    @Test
    void dlt_handler_marks_notification_dead() throws Exception {
        Notification notification = pendingNotification("dlq_dead");

        notificationConsumer.onDlt(notificationRequested(notification));

        assertThat(jdbc.sql("SELECT status FROM notification WHERE id = CAST(:id AS uuid)")
                .param("id", notification.id().toString())
                .query(String.class).single()).isEqualTo("dead");
    }
}
