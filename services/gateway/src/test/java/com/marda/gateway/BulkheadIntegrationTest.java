package com.marda.gateway;

import io.github.resilience4j.bulkhead.BulkheadRegistry;
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
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
class BulkheadIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";
    private static final MockWebServer DOWNSTREAM = startDownstream();

    @Autowired
    WebTestClient webTestClient;

    @Autowired
    BulkheadRegistry bulkheadRegistry;

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
        registry.add("gateway.resilience.bulkhead-max-concurrent-calls", () -> 1);
        // 一次失败就足以打开熔断——用来证明舱壁拒绝没有计入熔断。
        registry.add("gateway.resilience.minimum-number-of-calls", () -> 1);
        registry.add("gateway.resilience.sliding-window-size", () -> 1);
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
    void second_concurrent_request_is_rejected_when_bulkhead_is_full() throws Exception {
        assertThat(bulkheadRegistry.find("learning").orElseThrow()
                .getBulkheadConfig().getMaxConcurrentCalls()).isEqualTo(1);

        DOWNSTREAM.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{}")
                // 延迟响应头，确保第一个请求的网关链一定还没完成、舱壁许可仍被占用。
                .setHeadersDelay(1, TimeUnit.SECONDS));
        String auth = "Bearer " + TestJwt.token("u_bulkhead");
        int requestsBefore = DOWNSTREAM.getRequestCount();

        CompletableFuture<WebTestClient.ResponseSpec> first = CompletableFuture.supplyAsync(() ->
                webTestClient.get().uri("/api/v1/learning/plans")
                        .header("Authorization", auth)
                        .exchange()
                        .expectStatus().isOk());

        // 等第一个请求真正到达下游，确保舱壁许可已占用。
        assertThat(DOWNSTREAM.takeRequest(2, TimeUnit.SECONDS)).isNotNull();

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isEqualTo(429)
                .expectBody()
                .jsonPath("$.code").isEqualTo("RATE_LIMITED");

        // 被舱壁拒绝的请求不应打到下游。
        assertThat(DOWNSTREAM.getRequestCount() - requestsBefore).isEqualTo(1);

        // 舱壁在熔断外层：拒绝不能被记成下游失败（min-calls=1 时一次失败就会 open）。
        CircuitBreaker breaker = circuitBreakerRegistry.find("learning").orElseThrow();
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
        assertThat(breaker.getMetrics().getNumberOfFailedCalls()).isZero();

        first.get(5, TimeUnit.SECONDS);

        // 第一个请求结束后许可释放，后续请求恢复。
        DOWNSTREAM.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", auth)
                .exchange()
                .expectStatus().isOk();
    }
}
