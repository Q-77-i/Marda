package com.marda.gateway;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.reactive.AutoConfigureWebTestClient;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
@Testcontainers
class RateLimitIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";

    @Container
    static final GenericContainer<?> REDIS = new GenericContainer<>(DockerImageName.parse("redis:7-alpine"))
            .withExposedPorts(6379);

    private static final MockWebServer DOWNSTREAM = startDownstream();

    @Autowired
    WebTestClient webTestClient;

    private static MockWebServer startDownstream() {
        MockWebServer server = new MockWebServer();
        try {
            server.start();
        } catch (IOException e) {
            throw new ExceptionInInitializerError(e);
        }
        return server;
    }

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("BUSINESS_URL", () -> DOWNSTREAM.url("/").toString().replaceAll("/$", ""));
        registry.add("gateway.jwt.secret", () -> TestJwt.SECRET);
        registry.add("gateway.service-token", () -> SERVICE_TOKEN);
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
        // 单 token 桶：第一个请求拿走唯一令牌，第二个立刻 429。
        registry.add("gateway.rate-limit.replenish-rate", () -> 1);
        registry.add("gateway.rate-limit.burst-capacity", () -> 1);
        registry.add("gateway.rate-limit.requested-tokens", () -> 1);
    }

    @AfterAll
    static void stopDownstream() throws IOException {
        DOWNSTREAM.shutdown();
    }

    @BeforeEach
    void drainDownstream() throws InterruptedException {
        while (DOWNSTREAM.takeRequest(50, TimeUnit.MILLISECONDS) != null) {
            // 清空上一个用例可能残留的请求
        }
    }

    @Test
    void second_request_same_user_is_rate_limited_with_retry_after_and_envelope() {
        String auth = "Bearer " + TestJwt.token("u_rl_limited");
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{\"items\":[]}"));
        int requestsBefore = DOWNSTREAM.getRequestCount();

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isOk();

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isEqualTo(429)
                .expectHeader().valueEquals("Retry-After", "1")
                .expectHeader().valueEquals("X-RateLimit-Remaining", "0")
                .expectBody()
                .jsonPath("$.code").isEqualTo("RATE_LIMITED")
                .jsonPath("$.request_id").isNotEmpty();

        // 被限流的请求不应打到下游。
        assertThat(DOWNSTREAM.getRequestCount() - requestsBefore).isEqualTo(1);
    }

    @Test
    void allowed_request_carries_rate_limit_headers() {
        String auth = "Bearer " + TestJwt.token("u_rl_allowed");
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{\"items\":[]}"));

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isOk()
                .expectHeader().valueEquals("X-RateLimit-Remaining", "0");
    }

    @Test
    void different_users_have_separate_buckets() {
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{\"items\":[]}"));
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{\"items\":[]}"));

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_rl_a"))
                .exchange()
                .expectStatus().isOk();

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_rl_b"))
                .exchange()
                .expectStatus().isOk();
    }
}
