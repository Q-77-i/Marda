package com.marda.business.plan;

import com.fasterxml.jackson.databind.JsonNode;
import com.marda.business.plan.domain.PlanDecision;
import com.marda.business.plan.domain.PlanDraft;
import com.marda.business.plan.domain.PlanFactory;
import com.marda.business.plan.domain.TaskDraft;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.SpyBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doReturn;

/**
 * 事务边界测试：任务插入失败时，计划头必须一起回滚。
 *
 * <p>用 spy 让工厂产出同域重复任务，触发数据库 `uq_learning_task_plan_domain`；
 * 这是故障注入，不是测实现细节——断言的是“计划 + 任务同一事务”这个公开语义。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class PlanTransactionTest {

    private static final String SERVICE_TOKEN = "test-service-token";

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
    }

    @SpyBean
    PlanFactory planFactory;

    @Autowired
    TestRestTemplate rest;

    @Autowired
    JdbcClient jdbc;

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE learning_task, learning_plan, notification, learning_plan_skip CASCADE")
                .update();
    }

    @Test
    void task_insert_failure_rolls_back_plan() {
        doReturn(new PlanDecision.Create(new PlanDraft("goal", List.of(
                new TaskDraft("rag", "a", Instant.parse("2026-11-11T15:59:59Z"), 1),
                new TaskDraft("rag", "b", Instant.parse("2026-11-12T15:59:59Z"), 2)))))
                .when(planFactory).decide(any());

        ResponseEntity<JsonNode> response = postPlan();

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(response.getBody().get("code").asText()).isEqualTo("CONFLICT");
        assertThat(jdbc.sql("SELECT count(*) FROM learning_plan").query(Long.class).single())
                .isZero();
        assertThat(jdbc.sql("SELECT count(*) FROM learning_task").query(Long.class).single())
                .isZero();
    }

    private ResponseEntity<JsonNode> postPlan() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set("X-Service-Token", SERVICE_TOKEN);
        headers.set("X-User-Id", "u1");
        headers.set("Idempotency-Key", "iv-broken");
        String body = """
                {
                  "source_interview_id": "iv-broken",
                  "position": "Agent",
                  "interview_type": "tech",
                  "report_summary": {
                    "overall": 3.0,
                    "weak_domains": [{"domain": "rag", "score": 2.0}]
                  }
                }
                """;
        return rest.exchange(
                "/api/v1/learning/plans", HttpMethod.POST,
                new HttpEntity<>(body, headers), JsonNode.class);
    }
}
