package com.marda.business.cache;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

/** 缓存失败语义：Redis 挂 / 缓存关闭时必须回源，不能把异常抛给业务。 */
class BusinessCacheTest {

    @Test
    void redis_down_falls_back_to_loader() {
        LettuceConnectionFactory factory = new LettuceConnectionFactory("127.0.0.1", 1);
        factory.setTimeout(200);
        factory.afterPropertiesSet();
        factory.start();
        try {
            StringRedisTemplate redis = new StringRedisTemplate(factory);
            redis.afterPropertiesSet();
            BusinessCache cache = new BusinessCache(
                    redis, BusinessCacheProperties.defaults(), new SimpleMeterRegistry());

            CacheResult<String> result = cache.getOrLoad(
                    "today", "k", String.class, () -> "from-db",
                    Duration.ofSeconds(60), Duration.ofSeconds(30), false);

            assertThat(result.value()).isEqualTo("from-db");
            assertThat(result.outcome()).isEqualTo(CacheOutcome.BYPASS);
        } finally {
            factory.destroy();
        }
    }

    @Test
    void disabled_cache_always_loads() {
        BusinessCacheProperties properties = new BusinessCacheProperties(
                false, "test:", null, null, null, 0.1, null, null, null);
        // 缓存关闭时不会碰 Redis，所以 client 可以为 null
        BusinessCache cache = new BusinessCache(null, properties, new SimpleMeterRegistry());

        CacheResult<String> first = cache.getOrLoad(
                "today", "k", String.class, () -> "db",
                Duration.ofSeconds(60), Duration.ofSeconds(30), false);
        CacheResult<String> second = cache.getOrLoad(
                "today", "k", String.class, () -> "db",
                Duration.ofSeconds(60), Duration.ofSeconds(30), false);

        assertThat(first.outcome()).isEqualTo(CacheOutcome.BYPASS);
        assertThat(second.outcome()).isEqualTo(CacheOutcome.BYPASS);
        assertThat(first.value()).isEqualTo("db");
        assertThat(second.value()).isEqualTo("db");
    }
}
