package com.marda.gateway;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
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
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
class GatewayIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";
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
        // 本类只验鉴权 / 转发，不验限流；关掉避免 Redis 依赖。
        registry.add("gateway.rate-limit.enabled", () -> false);
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
    void health_is_public() {
        webTestClient.get().uri("/actuator/health")
                .exchange()
                .expectStatus().isOk();
    }

    @Test
    void learning_route_requires_auth() {
        webTestClient.get().uri("/api/v1/learning/plans")
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void invalid_jwt_is_rejected() {
        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer not-a-jwt")
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void valid_jwt_forwards_authenticated_user_header() throws Exception {
        DOWNSTREAM.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"items\":[]}"));

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_123"))
                .header("X-User-Id", "attacker")
                .header("X-Service-Token", "attacker")
                .exchange()
                .expectStatus().isOk()
                .expectBody().json("{\"items\":[]}");

        RecordedRequest request = DOWNSTREAM.takeRequest(2, TimeUnit.SECONDS);
        assertThat(request).isNotNull();
        assertThat(request.getHeader("X-User-Id")).isEqualTo("u_123");
        assertThat(request.getHeader("X-Request-Id")).isNotBlank();
        assertThat(request.getHeader("traceparent")).isNotBlank();
        // 用户 JWT 只用于 gateway 鉴权，不转给 business。
        assertThat(request.getHeader("Authorization")).isNull();
        // 客户端自带的 service token 被覆盖，只信 gateway 配置。
        assertThat(request.getHeader("X-Service-Token")).isEqualTo(SERVICE_TOKEN);
    }
}
