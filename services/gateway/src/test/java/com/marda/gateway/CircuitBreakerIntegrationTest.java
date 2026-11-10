package com.marda.gateway;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
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

import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
class CircuitBreakerIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";
    private static final MockWebServer DOWNSTREAM = startDownstream();

    @Autowired
    WebTestClient webTestClient;

    @Autowired
    CircuitBreakerRegistry circuitBreakerRegistry;

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
        registry.add("gateway.rate-limit.enabled", () -> false);
        registry.add("gateway.resilience.sliding-window-size", () -> 4);
        registry.add("gateway.resilience.minimum-number-of-calls", () -> 4);
        registry.add("gateway.resilience.failure-rate-threshold", () -> 50);
        registry.add("gateway.resilience.wait-duration-in-open-state", () -> "10s");
        registry.add("gateway.resilience.permitted-number-of-calls-in-half-open-state", () -> 2);
        registry.add("gateway.resilience.timeout-duration", () -> "500ms");
        registry.add("gateway.resilience.bulkhead-max-concurrent-calls", () -> 100);
    }

    @AfterAll
    static void stopDownstream() throws IOException {
        DOWNSTREAM.shutdown();
    }

    @BeforeEach
    void reset() throws InterruptedException {
        circuitBreakerRegistry.find("learning").ifPresent(CircuitBreaker::reset);
        while (DOWNSTREAM.takeRequest(50, TimeUnit.MILLISECONDS) != null) {
            // 清空上一个用例可能残留的请求
        }
    }

    @Test
    void five_xx_opens_circuit_and_next_request_gets_503_without_hitting_downstream() {
        for (int i = 0; i < 4; i++) {
            // 500/501 交替：501 也必须计入失败，否则 4 次里只有 2 次失败，熔断不会打开。
            DOWNSTREAM.enqueue(new MockResponse()
                    .setResponseCode(i % 2 == 0 ? 500 : 501)
                    .setBody("boom"));
        }
        String auth = "Bearer " + TestJwt.token("u_cb_open");

        for (int i = 0; i < 4; i++) {
            webTestClient.get().uri("/api/v1/learning/plans")
                    .header("Authorization", auth)
                    .exchange()
                    .expectStatus().is5xxServerError();
        }
        assertThat(circuitBreakerRegistry.find("learning").orElseThrow().getState())
                .isEqualTo(CircuitBreaker.State.OPEN);

        int requestsBefore = DOWNSTREAM.getRequestCount();
        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isEqualTo(503)
                .expectBody()
                .jsonPath("$.code").isEqualTo("DEPENDENCY_UNAVAILABLE")
                .jsonPath("$.request_id").isNotEmpty();

        // 熔断打开后不应再打到下游。
        assertThat(DOWNSTREAM.getRequestCount()).isEqualTo(requestsBefore);
    }

    @Test
    void slow_downstream_hits_timeout_and_returns_503() {
        DOWNSTREAM.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{}")
                // 延迟响应头：网关的 Mono 才会一直等待，TimeLimiter 才有东西可超时。
                .setHeadersDelay(3, TimeUnit.SECONDS));
        String auth = "Bearer " + TestJwt.token("u_cb_timeout");

        long started = System.nanoTime();
        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isEqualTo(503)
                .expectBody()
                .jsonPath("$.code").isEqualTo("DEPENDENCY_UNAVAILABLE");

        assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(2));
    }

    @Test
    void half_open_closes_after_successful_calls() {
        // 先发一个正常请求，让 factory 用我们的配置创建 breaker；
        // 直接 registry.circuitBreaker(name) 会先建默认配置，factory 之后不会覆盖。
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        String auth = "Bearer " + TestJwt.token("u_cb_half_open");
        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isOk();

        CircuitBreaker breaker = circuitBreakerRegistry.find("learning").orElseThrow();
        breaker.transitionToOpenState();
        breaker.transitionToHalfOpenState();

        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        for (int i = 0; i < 2; i++) {
            webTestClient.get().uri("/api/v1/learning/plans")
                    .header("Authorization", auth)
                    .exchange()
                    .expectStatus().isOk();
        }

        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
    }
}
