package com.marda.gateway;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Component
@ConfigurationProperties(prefix = "gateway.resilience")
public class GatewayResilienceProperties {

    private boolean bulkheadEnabled = true;

    /** 每个网关实例同时打到下游的最大请求数。 */
    private int bulkheadMaxConcurrentCalls = 20;

    private int slidingWindowSize = 20;

    private int minimumNumberOfCalls = 10;

    private float failureRateThreshold = 50f;

    private Duration waitDurationInOpenState = Duration.ofSeconds(10);

    private int permittedNumberOfCallsInHalfOpenState = 5;

    private boolean automaticTransitionFromOpenToHalfOpenEnabled = true;

    /** 单次下游调用的总超时（Resilience4j TimeLimiter）。 */
    private Duration timeoutDuration = Duration.ofSeconds(2);

    public boolean isBulkheadEnabled() {
        return bulkheadEnabled;
    }

    public void setBulkheadEnabled(boolean bulkheadEnabled) {
        this.bulkheadEnabled = bulkheadEnabled;
    }

    public int getBulkheadMaxConcurrentCalls() {
        return bulkheadMaxConcurrentCalls;
    }

    public void setBulkheadMaxConcurrentCalls(int bulkheadMaxConcurrentCalls) {
        this.bulkheadMaxConcurrentCalls = bulkheadMaxConcurrentCalls;
    }

    public int getSlidingWindowSize() {
        return slidingWindowSize;
    }

    public void setSlidingWindowSize(int slidingWindowSize) {
        this.slidingWindowSize = slidingWindowSize;
    }

    public int getMinimumNumberOfCalls() {
        return minimumNumberOfCalls;
    }

    public void setMinimumNumberOfCalls(int minimumNumberOfCalls) {
        this.minimumNumberOfCalls = minimumNumberOfCalls;
    }

    public float getFailureRateThreshold() {
        return failureRateThreshold;
    }

    public void setFailureRateThreshold(float failureRateThreshold) {
        this.failureRateThreshold = failureRateThreshold;
    }

    public Duration getWaitDurationInOpenState() {
        return waitDurationInOpenState;
    }

    public void setWaitDurationInOpenState(Duration waitDurationInOpenState) {
        this.waitDurationInOpenState = waitDurationInOpenState;
    }

    public int getPermittedNumberOfCallsInHalfOpenState() {
        return permittedNumberOfCallsInHalfOpenState;
    }

    public void setPermittedNumberOfCallsInHalfOpenState(int permittedNumberOfCallsInHalfOpenState) {
        this.permittedNumberOfCallsInHalfOpenState = permittedNumberOfCallsInHalfOpenState;
    }

    public boolean isAutomaticTransitionFromOpenToHalfOpenEnabled() {
        return automaticTransitionFromOpenToHalfOpenEnabled;
    }

    public void setAutomaticTransitionFromOpenToHalfOpenEnabled(
            boolean automaticTransitionFromOpenToHalfOpenEnabled) {
        this.automaticTransitionFromOpenToHalfOpenEnabled = automaticTransitionFromOpenToHalfOpenEnabled;
    }

    public Duration getTimeoutDuration() {
        return timeoutDuration;
    }

    public void setTimeoutDuration(Duration timeoutDuration) {
        this.timeoutDuration = timeoutDuration;
    }
}
