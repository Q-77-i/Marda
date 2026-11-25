package com.marda.business.plan;

import com.fasterxml.jackson.databind.JsonNode;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * P5-J4 缓存：最高接缝（真实 HTTP + Spring + PG + Redis）。
 *
 * <p>钉三件事：第二次读命中；写后缓存失效（读己之写）；缓存键按用户隔离。
 * Redis 用 Testcontainers 起真容器——单飞 / 序列化 / 失效都必须真 Redis 才验得到。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class PlanCacheIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";

    @Container
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>("postgres:16-alpine")
                    .withDatabaseName("marda_business")
                    .withUsername("marda")
                    .withPassword("marda");

    @Container
    static final GenericContainer<?> REDIS =
            new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
        registry.add("spring.data.redis.database", () -> "1");
        registry.add("business.cache.enabled", () -> "true");
        registry.add("business.service-token", () -> SERVICE_TOKEN);
        registry.add("business.timezone", () -> "Asia/Shanghai");
    }

    @TestConfiguration
    static class FixedClockConfig {
        @Bean
        @Primary
        Clock fixedClock() {
            return Clock.fixed(Instant.parse("2026-11-11T02:00:00Z"), ZoneOffset.UTC);
        }
    }

    @Autowired
    TestRestTemplate rest;

    @Autowired
    JdbcClient jdbc;

    @Autowired
    MeterRegistry meterRegistry;

    /** 每个用例独立用户：缓存键不串，且不必 FLUSHDB。 */
    private final String user = "u-" + UUID.randomUUID();

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE learning_task, learning_plan, notification, learning_plan_skip, business_outbox, processed_event CASCADE")
                .update();
    }

    @Test
    void today_second_read_hits_and_task_update_invalidates() {
        JsonNode plan = createPlan();
        String taskId = plan.get("tasks").get(0).get("id").asText();

        ResponseEntity<JsonNode> first = get("/api/v1/learning/today");
        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(first.getHeaders().getFirst("X-Cache")).isEqualTo("MISS");

        ResponseEntity<JsonNode> second = get("/api/v1/learning/today");
        assertThat(second.getHeaders().getFirst("X-Cache")).isEqualTo("HIT");
        assertThat(second.getBody()).isEqualTo(first.getBody());
        assertThat(meterRegistry.get("cache.requests")
                .tag("cache", "learning.today")
                .tag("result", "HIT")
                .counter().count()).isGreaterThanOrEqualTo(1.0);

        ResponseEntity<JsonNode> patched = patchTask(taskId, 1, "in_progress");
        assertThat(patched.getStatusCode()).isEqualTo(HttpStatus.OK);

        ResponseEntity<JsonNode> after = get("/api/v1/learning/today");
        assertThat(after.getHeaders().getFirst("X-Cache")).isEqualTo("MISS");
        assertThat(after.getBody().get("tasks").get(0).get("status").asText())
                .isEqualTo("in_progress");
    }

    @Test
    void plan_detail_second_read_hits_and_task_update_invalidates() {
        JsonNode plan = createPlan();
        String planId = plan.get("id").asText();
        String taskId = plan.get("tasks").get(0).get("id").asText();
        String path = "/api/v1/learning/plans/" + planId;

        ResponseEntity<JsonNode> first = get(path);
        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(first.getHeaders().getFirst("X-Cache")).isEqualTo("MISS");

        ResponseEntity<JsonNode> second = get(path);
        assertThat(second.getHeaders().getFirst("X-Cache")).isEqualTo("HIT");
        assertThat(second.getBody()).isEqualTo(first.getBody());

        assertThat(patchTask(taskId, 1, "in_progress").getStatusCode()).isEqualTo(HttpStatus.OK);

        ResponseEntity<JsonNode> after = get(path);
        assertThat(after.getHeaders().getFirst("X-Cache")).isEqualTo("MISS");
        assertThat(after.getBody().get("tasks").get(0).get("status").asText())
                .isEqualTo("in_progress");
    }

    @Test
    void cache_key_includes_user_so_other_user_cannot_read_it() {
        JsonNode plan = createPlan();
        String path = "/api/v1/learning/plans/" + plan.get("id").asText();

        HttpHeaders headers = authHeaders("other-" + UUID.randomUUID());
        ResponseEntity<JsonNode> denied = rest.exchange(
                path, HttpMethod.GET, new HttpEntity<>(headers), JsonNode.class);

        assertThat(denied.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    }

    private JsonNode createPlan() {
        String sourceId = "iv-" + UUID.randomUUID();
        String body = """
                {
                  "source_interview_id": "%s",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {
                    "overall": 3.4,
                    "weak_domains": [
                      {"domain": "rag", "score": 2.5, "advice": "补混合检索"}
                    ]
                  }
                }
                """.formatted(sourceId);
        HttpHeaders headers = authHeaders(user);
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set("Idempotency-Key", sourceId);
        ResponseEntity<JsonNode> response = rest.exchange(
                "/api/v1/learning/plans", HttpMethod.POST,
                new HttpEntity<>(body, headers), JsonNode.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(response.getBody()).isNotNull();
        return response.getBody();
    }

    private ResponseEntity<JsonNode> get(String path) {
        return rest.exchange(
                path, HttpMethod.GET, new HttpEntity<>(authHeaders(user)), JsonNode.class);
    }

    private ResponseEntity<JsonNode> patchTask(String taskId, long version, String status) {
        HttpHeaders headers = authHeaders(user);
        headers.setContentType(MediaType.APPLICATION_JSON);
        String body = """
                {"status": "%s", "version": %d}
                """.formatted(status, version);
        return rest.exchange(
                "/api/v1/learning/tasks/" + taskId, HttpMethod.PATCH,
                new HttpEntity<>(body, headers), JsonNode.class);
    }

    private HttpHeaders authHeaders(String userId) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", userId);
        return headers;
    }
}
