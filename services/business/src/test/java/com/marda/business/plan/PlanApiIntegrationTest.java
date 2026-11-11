package com.marda.business.plan;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
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
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 最高接缝：真实 HTTP + 过滤器 + service + JdbcClient + PostgreSQL。
 *
 * <p>固定 Clock 让截止日可断言；每个用例前清空四张表，避免相互污染。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureMockMvc
@Testcontainers
class PlanApiIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";
    private static final String USER = "u1";

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
        registry.add("business.service-token", () -> SERVICE_TOKEN);
        registry.add("business.timezone", () -> "Asia/Shanghai");
        // 测试专用探针路径；生产 permit 列表只有 actuator / error。
        registry.add("business.security.permit-paths", () ->
                "/actuator/health,/actuator/health/**,/actuator/info,/error,/test/**");
    }

    @TestConfiguration
    static class FixedClockConfig {
        @Bean
        @Primary
        Clock fixedClock() {
            return Clock.fixed(Instant.parse("2026-11-11T02:00:00Z"), ZoneOffset.UTC);
        }
    }

    @TestConfiguration
    @Import(VirtualThreadProbeConfig.ProbeController.class)
    static class VirtualThreadProbeConfig {

        @RestController
        static class ProbeController {
            @GetMapping("/test/thread")
            Map<String, Object> thread() {
                return Map.of(
                        "virtual", Thread.currentThread().isVirtual(),
                        "name", Thread.currentThread().toString());
            }
        }
    }

    @Autowired
    TestRestTemplate rest;

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper mapper;

    @Autowired
    JdbcClient jdbc;

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE learning_task, learning_plan, notification, learning_plan_skip CASCADE")
                .update();
    }

    @Test
    void creates_plan_from_report_summary() {
        ResponseEntity<JsonNode> response = postPlan("""
                {
                  "source_interview_id": "iv-1",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {
                    "overall": 3.4,
                    "weak_domains": [
                      {"domain": "rag", "score": 2.5, "advice": "补混合检索"},
                      {"domain": "memory", "score": 3.0, "advice": "补压缩策略"}
                    ]
                  }
                }
                """, "iv-1");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        JsonNode body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.get("id").asText()).isNotBlank();
        assertThat(body.get("user_id").asText()).isEqualTo(USER);
        assertThat(body.get("source_interview_id").asText()).isEqualTo("iv-1");
        assertThat(body.get("goal").asText()).isEqualTo("针对「Agent」的面试短板学习计划");
        assertThat(body.get("status").asText()).isEqualTo("active");
        assertThat(body.get("version").asLong()).isEqualTo(1);
        assertThat(body.get("tasks")).hasSize(2);
        assertThat(body.get("tasks").get(0).get("domain").asText()).isEqualTo("rag");
        assertThat(body.get("tasks").get(0).get("priority").asInt()).isEqualTo(1);
        assertThat(body.get("tasks").get(0).get("status").asText()).isEqualTo("todo");
        assertThat(body.get("tasks").get(0).get("due_at").asText())
                .isEqualTo("2026-11-11T15:59:59Z");
        assertThat(body.get("tasks").get(1).get("domain").asText()).isEqualTo("memory");
        assertThat(body.get("tasks").get(1).get("priority").asInt()).isEqualTo(2);
    }

    @Test
    void same_key_and_body_returns_existing_plan() {
        String body = """
                {
                  "source_interview_id": "iv-1",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {
                    "overall": 3.4,
                    "weak_domains": [
                      {"domain": "rag", "score": 2.5, "advice": "补混合检索"}
                    ]
                  }
                }
                """;

        ResponseEntity<JsonNode> first = postPlan(body, "iv-1");
        ResponseEntity<JsonNode> second = postPlan(body, "iv-1");

        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(second.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(second.getBody().get("id").asText())
                .isEqualTo(first.getBody().get("id").asText());
    }

    @Test
    void same_key_with_different_body_is_idempotency_conflict() {
        ResponseEntity<JsonNode> first = postPlan(planBody("目标 A"), "iv-1");
        ResponseEntity<JsonNode> second = postPlan(planBody("目标 B"), "iv-1");

        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(second.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(second.getBody().get("code").asText()).isEqualTo("IDEMPOTENCY_CONFLICT");
    }

    @Test
    void idempotency_key_must_equal_source_interview_id() {
        ResponseEntity<JsonNode> response = postPlan(planBody("目标"), "other-key");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody().get("code").asText()).isEqualTo("BAD_REQUEST");
    }

    @Test
    void missing_user_header_is_unauthorized() throws Exception {
        // TestRestTemplate 的 HttpURLConnection 对带 body 的 401 会尝试认证重试，
        // 用 MockMvc 走同一套过滤器断言状态与信封。
        mockMvc.perform(post("/api/v1/learning/plans")
                        .header("X-Service-Token", SERVICE_TOKEN)
                        .header("Idempotency-Key", "iv-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(planBody("目标")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                .andExpect(jsonPath("$.request_id").isNotEmpty());
    }

    @Test
    void wrong_service_token_is_forbidden() {
        HttpHeaders headers = jsonHeaders();
        headers.set("X-Service-Token", "wrong-token");
        headers.set("X-User-Id", USER);
        headers.set("Idempotency-Key", "iv-1");

        ResponseEntity<JsonNode> response = rest.exchange(
                "/api/v1/learning/plans", HttpMethod.POST,
                new HttpEntity<>(planBody("目标"), headers), JsonNode.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(response.getBody().get("code").asText()).isEqualTo("FORBIDDEN");
        assertThat(response.getBody().get("request_id").asText()).isNotBlank();
    }

    @Test
    void requests_run_on_virtual_threads() {
        ResponseEntity<JsonNode> response = rest.exchange(
                "/test/thread", HttpMethod.GET, HttpEntity.EMPTY, JsonNode.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody().get("virtual").asBoolean()).isTrue();
    }

    @Test
    void patch_task_can_clear_and_change_due_date() {
        ResponseEntity<JsonNode> created = postPlan(planBody("目标"), "iv-1");
        String taskId = created.getBody().get("tasks").get(0).get("id").asText();

        ResponseEntity<JsonNode> cleared = patchTask(
                USER, taskId, "{\"due_at\":null,\"version\":1}");
        assertThat(cleared.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(cleared.getBody().get("due_at").isNull()).isTrue();
        assertThat(cleared.getBody().get("version").asLong()).isEqualTo(2);

        ResponseEntity<JsonNode> changed = patchTask(
                USER, taskId, "{\"due_at\":\"2026-11-20T15:59:59Z\",\"version\":2}");
        assertThat(changed.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(changed.getBody().get("due_at").asText())
                .isEqualTo("2026-11-20T15:59:59Z");
        assertThat(changed.getBody().get("version").asLong()).isEqualTo(3);
    }

    @Test
    void terminal_task_due_date_cannot_change() {
        ResponseEntity<JsonNode> created = postPlan(planBody("目标"), "iv-1");
        String taskId = created.getBody().get("tasks").get(0).get("id").asText();

        assertThat(patchTask(USER, taskId, "{\"status\":\"done\",\"version\":1}").getStatusCode())
                .isEqualTo(HttpStatus.OK);
        ResponseEntity<JsonNode> response = patchTask(
                USER, taskId, "{\"due_at\":null,\"version\":2}");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(response.getBody().get("code").asText()).isEqualTo("STATE_CONFLICT");
    }

    @Test
    void patch_task_updates_status_and_auto_completes_plan() {
        ResponseEntity<JsonNode> created = postPlan(planBody("目标"), "iv-1");
        String planId = created.getBody().get("id").asText();
        String taskId = created.getBody().get("tasks").get(0).get("id").asText();

        ResponseEntity<JsonNode> patched = patchTask(
                USER, taskId, "{\"status\":\"done\",\"version\":1}");

        assertThat(patched.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(patched.getBody().get("status").asText()).isEqualTo("done");
        assertThat(patched.getBody().get("version").asLong()).isEqualTo(2);

        ResponseEntity<JsonNode> plan = get(USER, "/api/v1/learning/plans/" + planId);
        assertThat(plan.getBody().get("status").asText()).isEqualTo("completed");
        assertThat(plan.getBody().get("version").asLong()).isEqualTo(2);
    }

    @Test
    void patch_task_with_stale_version_is_version_conflict() {
        ResponseEntity<JsonNode> created = postPlan(planBody("目标"), "iv-1");
        String taskId = created.getBody().get("tasks").get(0).get("id").asText();

        assertThat(patchTask(USER, taskId, "{\"status\":\"done\",\"version\":1}").getStatusCode())
                .isEqualTo(HttpStatus.OK);
        ResponseEntity<JsonNode> stale = patchTask(
                USER, taskId, "{\"status\":\"done\",\"version\":1}");

        assertThat(stale.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(stale.getBody().get("code").asText()).isEqualTo("VERSION_CONFLICT");
        assertThat(stale.getBody().get("details").get("current_version").asLong()).isEqualTo(2);
    }

    @Test
    void terminal_task_cannot_go_back() {
        ResponseEntity<JsonNode> created = postPlan(planBody("目标"), "iv-1");
        String taskId = created.getBody().get("tasks").get(0).get("id").asText();

        assertThat(patchTask(USER, taskId, "{\"status\":\"done\",\"version\":1}").getStatusCode())
                .isEqualTo(HttpStatus.OK);
        ResponseEntity<JsonNode> backwards = patchTask(
                USER, taskId, "{\"status\":\"in_progress\",\"version\":2}");

        assertThat(backwards.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(backwards.getBody().get("code").asText()).isEqualTo("STATE_CONFLICT");
    }

    @Test
    void cross_user_plan_and_task_are_not_found() {
        ResponseEntity<JsonNode> created = postPlan(planBody("目标"), "iv-1");
        String planId = created.getBody().get("id").asText();
        String taskId = created.getBody().get("tasks").get(0).get("id").asText();

        assertThat(get("u2", "/api/v1/learning/plans/" + planId).getStatusCode())
                .isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(patchTask("u2", taskId, "{\"status\":\"done\",\"version\":1}").getStatusCode())
                .isEqualTo(HttpStatus.NOT_FOUND);
    }

    @Test
    void list_today_and_next_return_expected_tasks() {
        ResponseEntity<JsonNode> created = postPlan("""
                {
                  "source_interview_id": "iv-1",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {
                    "overall": 3.4,
                    "weak_domains": [
                      {"domain": "rag", "score": 2.5, "advice": "补混合检索"},
                      {"domain": "memory", "score": 3.0, "advice": "补压缩策略"}
                    ]
                  }
                }
                """, "iv-1");
        String planId = created.getBody().get("id").asText();

        ResponseEntity<JsonNode> list = get(USER, "/api/v1/learning/plans?status=active");
        assertThat(list.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(list.getBody().get("total").asLong()).isEqualTo(1);
        assertThat(list.getBody().get("items").get(0).get("tasks")).hasSize(2);

        ResponseEntity<JsonNode> today = get(USER, "/api/v1/learning/today");
        assertThat(today.getBody().get("date").asText()).isEqualTo("2026-11-11");
        assertThat(today.getBody().get("tasks")).hasSize(1);
        assertThat(today.getBody().get("tasks").get(0).get("domain").asText()).isEqualTo("rag");

        ResponseEntity<JsonNode> next = get(
                USER, "/internal/v1/learning-plans/u1/next?limit=3");
        assertThat(next.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(next.getBody().get("plan_id").asText()).isEqualTo(planId);
        assertThat(next.getBody().get("tasks")).hasSize(2);
        assertThat(next.getBody().get("generated_at").asText()).isNotBlank();
    }

    @Test
    void empty_weak_domains_is_unprocessable_and_audited() {
        ResponseEntity<JsonNode> response = postPlan("""
                {
                  "source_interview_id": "iv-empty",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {"overall": 0, "weak_domains": []}
                }
                """, "iv-empty");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.UNPROCESSABLE_ENTITY);
        assertThat(response.getBody().get("code").asText()).isEqualTo("NO_WEAK_DOMAINS");
        assertThat(jdbc.sql("""
                SELECT count(*) FROM learning_plan_skip
                WHERE source_interview_id = 'iv-empty' AND reason = 'NO_WEAK_DOMAINS'
                """).query(Long.class).single()).isEqualTo(1);
    }

    @Test
    void behavioral_interview_is_unprocessable_and_audited() {
        ResponseEntity<JsonNode> response = postPlan("""
                {
                  "source_interview_id": "iv-behavioral",
                  "position": "Agent",
                  "interview_type": "behavioral",
                  "report_summary": {
                    "overall": 3.0,
                    "weak_domains": [{"domain": "communication", "score": 2.0}]
                  }
                }
                """, "iv-behavioral");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.UNPROCESSABLE_ENTITY);
        assertThat(response.getBody().get("code").asText()).isEqualTo("UNSUPPORTED_INTERVIEW_TYPE");
        assertThat(jdbc.sql("""
                SELECT count(*) FROM learning_plan_skip
                WHERE source_interview_id = 'iv-behavioral'
                  AND reason = 'UNSUPPORTED_INTERVIEW_TYPE'
                """).query(Long.class).single()).isEqualTo(1);
    }

    @Test
    void internal_from_report_creates_then_replays() {
        String event = """
                {
                  "interview_id": "iv-internal-1",
                  "user_id": "u1",
                  "position": "Agent",
                  "interview_type": "tech",
                  "finished_at": "2026-11-10T11:59:30Z",
                  "report_id": "r1",
                  "report_summary": {
                    "overall": 3.4,
                    "weak_domains": [
                      {"domain": "rag", "score": 2.5, "advice": "补混合检索"}
                    ]
                  }
                }
                """;

        ResponseEntity<JsonNode> first = postInternal(event, USER);
        ResponseEntity<JsonNode> second = postInternal(event, USER);

        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(second.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(second.getBody().get("id").asText())
                .isEqualTo(first.getBody().get("id").asText());
    }

    @Test
    void internal_from_report_empty_weak_domains_returns_no_content() {
        ResponseEntity<JsonNode> response = postInternal("""
                {
                  "interview_id": "iv-internal-empty",
                  "user_id": "u1",
                  "position": "Agent",
                  "interview_type": "tech",
                  "finished_at": "2026-11-10T11:59:30Z",
                  "report_id": "r1",
                  "report_summary": {"overall": 0, "weak_domains": []}
                }
                """, USER);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NO_CONTENT);
        assertThat(jdbc.sql("""
                SELECT count(*) FROM learning_plan_skip
                WHERE source_interview_id = 'iv-internal-empty'
                """).query(Long.class).single()).isEqualTo(1);
    }

    @Test
    void internal_from_report_user_header_mismatch_is_bad_request() {
        ResponseEntity<JsonNode> response = postInternal("""
                {
                  "interview_id": "iv-internal-mismatch",
                  "user_id": "u1",
                  "position": "Agent",
                  "interview_type": "tech",
                  "finished_at": "2026-11-10T11:59:30Z",
                  "report_id": "r1",
                  "report_summary": {
                    "overall": 3.0,
                    "weak_domains": [{"domain": "rag", "score": 2.0}]
                  }
                }
                """, "u2");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody().get("code").asText()).isEqualTo("BAD_REQUEST");
    }

    @Test
    void reminder_test_writes_pending_notification() {
        HttpHeaders headers = jsonHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", USER);

        ResponseEntity<JsonNode> response = rest.exchange(
                "/api/v1/learning/reminders/test", HttpMethod.POST,
                new HttpEntity<>("{\"channel\":\"in_app\"}", headers), JsonNode.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        assertThat(response.getBody().get("status").asText()).isEqualTo("pending");
        assertThat(jdbc.sql("""
                SELECT count(*) FROM notification
                WHERE user_id = 'u1' AND status = 'pending' AND channel = 'in_app'
                """).query(Long.class).single()).isEqualTo(1);
    }

    @Test
    void concurrent_create_produces_exactly_one_plan() throws Exception {
        CountDownLatch start = new CountDownLatch(1);
        ConcurrentLinkedQueue<Integer> statuses = new ConcurrentLinkedQueue<>();
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            for (int i = 0; i < 2; i++) {
                executor.submit(() -> {
                    try {
                        start.await();
                        statuses.add(postPlan(
                                planBody("iv-concurrent", "目标"), "iv-concurrent")
                                .getStatusCode().value());
                    } catch (InterruptedException exception) {
                        Thread.currentThread().interrupt();
                    }
                });
            }
            start.countDown();
            executor.shutdown();
            assertThat(executor.awaitTermination(20, TimeUnit.SECONDS)).isTrue();
        } finally {
            executor.shutdownNow();
        }

        assertThat(statuses).containsExactlyInAnyOrder(201, 200);
        assertThat(jdbc.sql("""
                SELECT count(*) FROM learning_plan WHERE source_interview_id = 'iv-concurrent'
                """).query(Long.class).single()).isEqualTo(1);
    }

    private ResponseEntity<JsonNode> patchTask(String userId, String taskId, String body) {
        HttpHeaders headers = jsonHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", userId);
        return rest.exchange(
                "/api/v1/learning/tasks/" + taskId, HttpMethod.PATCH,
                new HttpEntity<>(body, headers), JsonNode.class);
    }

    private ResponseEntity<JsonNode> get(String userId, String path) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", userId);
        return rest.exchange(path, HttpMethod.GET, new HttpEntity<>(headers), JsonNode.class);
    }

    private ResponseEntity<JsonNode> postInternal(String body, String userId) {
        HttpHeaders headers = jsonHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", userId);
        return rest.exchange(
                "/internal/v1/learning-plans/from-report", HttpMethod.POST,
                new HttpEntity<>(body, headers), JsonNode.class);
    }

    private ResponseEntity<JsonNode> postPlan(String body, String headerKey) {
        HttpHeaders headers = jsonHeaders();
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", USER);
        headers.set("Idempotency-Key", headerKey);
        return rest.exchange(
                "/api/v1/learning/plans", HttpMethod.POST,
                new HttpEntity<>(body, headers), JsonNode.class);
    }

    private HttpHeaders jsonHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return headers;
    }

    private static String planBody(String goal) {
        return planBody("iv-1", goal);
    }

    private static String planBody(String sourceInterviewId, String goal) {
        return """
                {
                  "source_interview_id": "%s",
                  "goal": "%s",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {
                    "overall": 3.4,
                    "weak_domains": [
                      {"domain": "rag", "score": 2.5, "advice": "补混合检索"}
                    ]
                  }
                }
                """.formatted(sourceInterviewId, goal);
    }
}
