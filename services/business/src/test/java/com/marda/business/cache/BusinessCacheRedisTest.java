package com.marda.business.cache;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Duration;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 分布式语义：两个缓存实例（模拟两个 business 实例）共用一个 Redis。
 *
 * <p>缓存里没有本地状态，A 写 B 必须立刻读到；A 删 B 必须未命中。
 * 真 Redis 才验得到——Mock 只能证明「调用了一个 set」。
 */
@Testcontainers
class BusinessCacheRedisTest {

    @Container
    static final GenericContainer<?> REDIS =
            new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Test
    void two_instances_share_cache_and_eviction() {
        LettuceConnectionFactory firstFactory = factory();
        LettuceConnectionFactory secondFactory = factory();
        try {
            StringRedisTemplate firstRedis = template(firstFactory);
            StringRedisTemplate secondRedis = template(secondFactory);
            BusinessCacheProperties properties = BusinessCacheProperties.defaults();
            BusinessCache first = new BusinessCache(
                    firstRedis, properties, new SimpleMeterRegistry());
            BusinessCache second = new BusinessCache(
                    secondRedis, properties, new SimpleMeterRegistry());

            String key = "shared-" + UUID.randomUUID();
            first.put(key, "v", Duration.ofSeconds(60));

            CacheResult<String> hit = second.get(key, String.class);
            assertThat(hit.outcome()).isEqualTo(CacheOutcome.HIT);
            assertThat(hit.value()).isEqualTo("v");

            first.evict(key);
            assertThat(second.get(key, String.class).outcome()).isEqualTo(CacheOutcome.MISS);
        } finally {
            firstFactory.destroy();
            secondFactory.destroy();
        }
    }

    private static LettuceConnectionFactory factory() {
        LettuceConnectionFactory factory = new LettuceConnectionFactory(
                REDIS.getHost(), REDIS.getMappedPort(6379));
        factory.setTimeout(500);
        factory.afterPropertiesSet();
        factory.start();
        return factory;
    }

    private static StringRedisTemplate template(LettuceConnectionFactory factory) {
        StringRedisTemplate template = new StringRedisTemplate(factory);
        template.afterPropertiesSet();
        return template;
    }
}
