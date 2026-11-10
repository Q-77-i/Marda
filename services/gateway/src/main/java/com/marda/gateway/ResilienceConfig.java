package com.marda.gateway;

import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.timelimiter.TimeLimiterConfig;
import org.springframework.cloud.circuitbreaker.resilience4j.ReactiveResilience4JCircuitBreakerFactory;
import org.springframework.cloud.circuitbreaker.resilience4j.Resilience4JConfigBuilder;
import org.springframework.cloud.client.circuitbreaker.Customizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Spring Cloud Circuit Breaker 的 Resilience4j factory 默认使用
 * {@code CircuitBreakerConfig.ofDefaults()} / {@code TimeLimiterConfig.ofDefaults()}（1s），
 * 不会读 {@code resilience4j.*} registry 默认值；必须显式 configureDefault。
 */
@Configuration
public class ResilienceConfig {

    @Bean
    Customizer<ReactiveResilience4JCircuitBreakerFactory> resilienceCustomizer(
            GatewayResilienceProperties properties) {
        return factory -> factory.configureDefault(id -> new Resilience4JConfigBuilder(id)
                .circuitBreakerConfig(CircuitBreakerConfig.custom()
                        .slidingWindowSize(properties.getSlidingWindowSize())
                        .minimumNumberOfCalls(properties.getMinimumNumberOfCalls())
                        .failureRateThreshold(properties.getFailureRateThreshold())
                        .waitDurationInOpenState(properties.getWaitDurationInOpenState())
                        .permittedNumberOfCallsInHalfOpenState(
                                properties.getPermittedNumberOfCallsInHalfOpenState())
                        .automaticTransitionFromOpenToHalfOpenEnabled(
                                properties.isAutomaticTransitionFromOpenToHalfOpenEnabled())
                        .build())
                .timeLimiterConfig(TimeLimiterConfig.custom()
                        .timeoutDuration(properties.getTimeoutDuration())
                        .cancelRunningFuture(true)
                        .build())
                .build());
    }
}
