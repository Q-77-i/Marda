package com.marda.gateway;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.proc.SecurityContext;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.reactive.AutoConfigureWebTestClient;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
class GatewayIntegrationTest {

    private static final String SECRET = "test-secret-0123456789abcdef0123456789";
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
        registry.add("gateway.jwt.secret", () -> SECRET);
        registry.add("gateway.service-token", () -> SERVICE_TOKEN);
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
                .header("Authorization", "Bearer " + token("u_123"))
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

    private static String token(String subject) {
        SecretKey key = new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
        JWKSource<SecurityContext> jwkSource = new ImmutableSecret<>(key);
        JwtEncoder encoder = new NimbusJwtEncoder(jwkSource);
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .subject(subject)
                .issuedAt(now)
                .expiresAt(now.plusSeconds(3600))
                .build();
        JwsHeader header = JwsHeader.with(MacAlgorithm.HS256).build();
        return encoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();
    }
}
