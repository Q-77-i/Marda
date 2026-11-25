package com.marda.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

class TokenBucketGatewayFilterFactoryTest {

    @Test
    void redis_timeout_fails_open_and_still_calls_downstream() {
        RedisRateLimiter limiter = Mockito.mock(RedisRateLimiter.class);
        when(limiter.isAllowed(anyString(), anyString())).thenReturn(Mono.never());

        GatewayRateLimitProperties properties = new GatewayRateLimitProperties();
        properties.setRedisTimeout(Duration.ofMillis(50));
        properties.setKeyResolveTimeout(Duration.ofMillis(50));

        TokenBucketGatewayFilterFactory factory = new TokenBucketGatewayFilterFactory(
                limiter, properties, new GatewayErrorWriter(new ObjectMapper()),
                new SimpleMeterRegistry());
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/v1/learning/plans").build());
        AtomicBoolean chainCalled = new AtomicBoolean();
        GatewayFilterChain chain = ex -> {
            chainCalled.set(true);
            return Mono.empty();
        };

        factory.apply(new TokenBucketGatewayFilterFactory.Config())
                .filter(exchange, chain)
                .block(Duration.ofSeconds(2));

        assertThat(chainCalled).isTrue();
        assertThat(exchange.getResponse().getStatusCode()).isNull();
    }
}
