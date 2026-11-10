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

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
class CanaryRoutingIntegrationTest {

    private static final String SERVICE_TOKEN = "test-service-token";
    private static final MockWebServer STABLE = startServer();
    private static final MockWebServer CANARY = startServer();

    @Autowired
    WebTestClient webTestClient;

    private static MockWebServer startServer() {
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
        registry.add("BUSINESS_URL", () -> STABLE.url("/").toString().replaceAll("/$", ""));
        registry.add("gateway.canary.canary-uri",
                () -> CANARY.url("/").toString().replaceAll("/$", ""));
        registry.add("gateway.canary.enabled", () -> true);
        registry.add("gateway.canary.percent", () -> 100);
        registry.add("gateway.canary.header-enabled", () -> true);
        registry.add("gateway.jwt.secret", () -> TestJwt.SECRET);
        registry.add("gateway.service-token", () -> SERVICE_TOKEN);
        registry.add("gateway.rate-limit.enabled", () -> false);
    }

    @AfterAll
    static void stopServers() throws IOException {
        STABLE.shutdown();
        CANARY.shutdown();
    }

    @BeforeEach
    void drainServers() throws InterruptedException {
        while (STABLE.takeRequest(20, TimeUnit.MILLISECONDS) != null) {
            // drain
        }
        while (CANARY.takeRequest(20, TimeUnit.MILLISECONDS) != null) {
            // drain
        }
    }

    @Test
    void percent_100_routes_user_traffic_to_canary() {
        CANARY.enqueue(new MockResponse().setResponseCode(200).setBody("{\"version\":\"canary\"}"));
        int stableBefore = STABLE.getRequestCount();
        int canaryBefore = CANARY.getRequestCount();

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_canary_100"))
                .exchange()
                .expectStatus().isOk()
                .expectBody().json("{\"version\":\"canary\"}");

        assertThat(CANARY.getRequestCount() - canaryBefore).isEqualTo(1);
        assertThat(STABLE.getRequestCount()).isEqualTo(stableBefore);
    }

    @Test
    void header_override_can_pin_traffic_to_stable() {
        STABLE.enqueue(new MockResponse().setResponseCode(200).setBody("{\"version\":\"stable\"}"));
        int canaryBefore = CANARY.getRequestCount();
        int stableBefore = STABLE.getRequestCount();

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_canary_header_stable"))
                .header("X-Canary", "stable")
                .header("X-Canary-Token", SERVICE_TOKEN)
                .exchange()
                .expectStatus().isOk()
                .expectBody().json("{\"version\":\"stable\"}");

        assertThat(STABLE.getRequestCount() - stableBefore).isEqualTo(1);
        assertThat(CANARY.getRequestCount()).isEqualTo(canaryBefore);
    }

    @Test
    void header_override_without_internal_token_is_ignored() {
        CANARY.enqueue(new MockResponse().setResponseCode(200).setBody("{\"version\":\"canary\"}"));

        // percent=100 时哈希本应走 canary；客户端自带的 X-Canary 没有内部 token，必须被忽略。
        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_canary_spoof"))
                .header("X-Canary", "stable")
                .exchange()
                .expectStatus().isOk()
                .expectBody().json("{\"version\":\"canary\"}");
    }

    @Test
    void header_override_can_pin_traffic_to_canary() {
        CANARY.enqueue(new MockResponse().setResponseCode(200).setBody("{\"version\":\"canary\"}"));

        webTestClient.get().uri("/api/v1/learning/plans")
                .header("Authorization", "Bearer " + TestJwt.token("u_canary_header_canary"))
                .header("X-Canary", "canary")
                .header("X-Canary-Token", SERVICE_TOKEN)
                .exchange()
                .expectStatus().isOk()
                .expectBody().json("{\"version\":\"canary\"}");
    }
}
