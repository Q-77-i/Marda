package com.marda.business.cache;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * 业务缓存配置。
 *
 * <p>默认关闭：本地单进程 / 单测不需要 Redis；compose 里通过
 * {@code BUSINESS_CACHE_ENABLED=true} 打开。缓存关闭 = 全部 BYPASS，业务照常。
 */
@ConfigurationProperties(prefix = "business.cache")
public record BusinessCacheProperties(
        boolean enabled,
        String keyPrefix,
        Duration todayTtl,
        Duration planTtl,
        Duration nullTtl,
        Double jitter,
        Duration lockTtl,
        Duration lockWait,
        Duration lockPoll) {

    public BusinessCacheProperties {
        keyPrefix = (keyPrefix == null || keyPrefix.isBlank()) ? "marda:cache:" : keyPrefix;
        todayTtl = todayTtl == null ? Duration.ofSeconds(60) : todayTtl;
        planTtl = planTtl == null ? Duration.ofMinutes(5) : planTtl;
        nullTtl = nullTtl == null ? Duration.ofSeconds(30) : nullTtl;
        jitter = jitter == null ? 0.1 : Math.max(0.0, jitter);
        lockTtl = lockTtl == null ? Duration.ofSeconds(3) : lockTtl;
        lockWait = lockWait == null ? Duration.ofMillis(200) : lockWait;
        lockPoll = lockPoll == null ? Duration.ofMillis(50) : lockPoll;
    }

    /** 单测用：打开缓存 + 标准 TTL。 */
    public static BusinessCacheProperties defaults() {
        return new BusinessCacheProperties(
                true, "marda:cache:", Duration.ofSeconds(60), Duration.ofMinutes(5),
                Duration.ofSeconds(30), 0.1, Duration.ofSeconds(3),
                Duration.ofMillis(200), Duration.ofMillis(50));
    }
}
