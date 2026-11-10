package com.marda.gateway;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Component
@ConfigurationProperties(prefix = "gateway.rate-limit")
public class GatewayRateLimitProperties {

    /** 关掉后过滤器直接放行；测试与本地无 Redis 时使用。 */
    private boolean enabled = true;

    /** 每秒补充的令牌数（Spring Cloud Gateway 的配置是 int，不能写小数）。 */
    private int replenishRate = 1;

    /** 桶容量，即允许的突发请求数。 */
    private int burstCapacity = 20;

    /** 每个请求消耗的令牌数。 */
    private int requestedTokens = 1;

    /** Redis 调用的硬上界：超时即 fail open，不能让限流把请求拖死。 */
    private Duration redisTimeout = Duration.ofMillis(200);

    /** 解析 user / IP key 的上界；解析失败也走 fail open。 */
    private Duration keyResolveTimeout = Duration.ofMillis(100);

    /** 429 的 Retry-After（秒）。 */
    private int retryAfterSeconds = 1;

    /** 是否信任 X-Forwarded-For；默认只信连接对端地址。 */
    private boolean trustForwardedFor = false;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public int getReplenishRate() {
        return replenishRate;
    }

    public void setReplenishRate(int replenishRate) {
        this.replenishRate = replenishRate;
    }

    public int getBurstCapacity() {
        return burstCapacity;
    }

    public void setBurstCapacity(int burstCapacity) {
        this.burstCapacity = burstCapacity;
    }

    public int getRequestedTokens() {
        return requestedTokens;
    }

    public void setRequestedTokens(int requestedTokens) {
        this.requestedTokens = requestedTokens;
    }

    public Duration getRedisTimeout() {
        return redisTimeout;
    }

    public void setRedisTimeout(Duration redisTimeout) {
        this.redisTimeout = redisTimeout;
    }

    public Duration getKeyResolveTimeout() {
        return keyResolveTimeout;
    }

    public void setKeyResolveTimeout(Duration keyResolveTimeout) {
        this.keyResolveTimeout = keyResolveTimeout;
    }

    public int getRetryAfterSeconds() {
        return retryAfterSeconds;
    }

    public void setRetryAfterSeconds(int retryAfterSeconds) {
        this.retryAfterSeconds = retryAfterSeconds;
    }

    public boolean isTrustForwardedFor() {
        return trustForwardedFor;
    }

    public void setTrustForwardedFor(boolean trustForwardedFor) {
        this.trustForwardedFor = trustForwardedFor;
    }
}
