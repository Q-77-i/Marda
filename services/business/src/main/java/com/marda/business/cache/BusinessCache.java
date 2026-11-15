package com.marda.business.cache;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;

/**
 * 业务缓存（P5-J4）：cache-aside + TTL 抖动 + 空值缓存 + SETNX 单飞 + 故障回源。
 *
 * <p>**失败语义**：缓存是加速，不是正确性来源。Redis 任何异常都只记日志并回源，
 * 不抛给业务；业务服务的 readiness 也不应因 Redis 挂掉变 DOWN（见 application.yml）。
 *
 * <p>值格式是版本信封 {@code {"v":1,"data":...}} / {@code {"v":1,"null":true}}；
 * 版本不匹配或 JSON 损坏按未命中处理，升级不炸旧缓存。
 */
@Component
public class BusinessCache {

    private static final Logger log = LoggerFactory.getLogger(BusinessCache.class);
    private static final int CACHE_VERSION = 1;
    private static final RedisScript<Long> UNLOCK = new DefaultRedisScript<>("""
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """, Long.class);

    private final StringRedisTemplate redis;
    private final BusinessCacheProperties properties;
    private final MeterRegistry meterRegistry;
    private final ObjectMapper mapper = JsonMapper.builder()
            .addModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .build();
    private final AtomicLong lastWarnNanos = new AtomicLong(0);

    public BusinessCache(
            StringRedisTemplate redis,
            BusinessCacheProperties properties,
            MeterRegistry meterRegistry) {
        this.redis = redis;
        this.properties = properties;
        this.meterRegistry = meterRegistry;
    }

    public <T> CacheResult<T> getOrLoad(
            String cacheName,
            String key,
            Class<T> type,
            Supplier<T> loader,
            Duration ttl,
            Duration nullTtl,
            boolean cacheNull) {
        if (!properties.enabled()) {
            return record(cacheName, new CacheResult<>(loader.get(), CacheOutcome.BYPASS));
        }

        CacheResult<T> lookup = get(key, type);
        if (lookup.outcome() == CacheOutcome.HIT) {
            return record(cacheName, lookup);
        }
        if (lookup.outcome() == CacheOutcome.BYPASS) {
            return record(cacheName, new CacheResult<>(loader.get(), CacheOutcome.BYPASS));
        }

        String token = UUID.randomUUID().toString();
        Boolean acquired = tryLock(key, token);
        if (acquired == null) {
            return record(cacheName, new CacheResult<>(loader.get(), CacheOutcome.BYPASS));
        }
        if (acquired) {
            try {
                // 双检：等锁期间别的请求可能已经回填。
                CacheResult<T> second = get(key, type);
                if (second.outcome() == CacheOutcome.HIT) {
                    return record(cacheName, second);
                }
                T value = loader.get();
                if (value != null || cacheNull) {
                    put(key, value, value == null ? nullTtl : ttl);
                }
                return record(cacheName, new CacheResult<>(value, CacheOutcome.MISS));
            } finally {
                unlock(key, token);
            }
        }

        // 没抢到锁：等赢家回填；等不到就回源，但不回填（避免失败者一起写）。
        long deadline = System.nanoTime() + properties.lockWait().toNanos();
        while (System.nanoTime() < deadline) {
            try {
                Thread.sleep(properties.lockPoll().toMillis());
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                break;
            }
            CacheResult<T> again = get(key, type);
            if (again.outcome() == CacheOutcome.HIT) {
                return record(cacheName, again);
            }
            if (again.outcome() == CacheOutcome.BYPASS) {
                break;
            }
        }
        return record(cacheName, new CacheResult<>(loader.get(), CacheOutcome.MISS));
    }

    public <T> CacheResult<T> get(String key, Class<T> type) {
        if (!properties.enabled()) {
            return new CacheResult<>(null, CacheOutcome.BYPASS);
        }
        String raw;
        try {
            raw = redis.opsForValue().get(fullKey(key));
        } catch (RuntimeException exception) {
            warn("GET", key, exception);
            return new CacheResult<>(null, CacheOutcome.BYPASS);
        }
        if (raw == null) {
            return new CacheResult<>(null, CacheOutcome.MISS);
        }
        try {
            JsonNode root = mapper.readTree(raw);
            if (root.path("v").asInt() != CACHE_VERSION) {
                return new CacheResult<>(null, CacheOutcome.MISS);
            }
            if (root.path("null").asBoolean(false)) {
                return new CacheResult<>(null, CacheOutcome.HIT);
            }
            JsonNode data = root.get("data");
            if (data == null || data.isNull()) {
                return new CacheResult<>(null, CacheOutcome.MISS);
            }
            return new CacheResult<>(mapper.treeToValue(data, type), CacheOutcome.HIT);
        } catch (Exception exception) { // noqa: 损坏值按未命中处理，随 TTL 过期
            return new CacheResult<>(null, CacheOutcome.MISS);
        }
    }

    public void put(String key, Object value, Duration ttl) {
        if (!properties.enabled()) {
            return;
        }
        try {
            ObjectNode root = mapper.createObjectNode();
            root.put("v", CACHE_VERSION);
            if (value == null) {
                root.put("null", true);
            } else {
                root.set("data", mapper.valueToTree(value));
            }
            redis.opsForValue().set(
                    fullKey(key), mapper.writeValueAsString(root), jittered(ttl));
        } catch (Exception exception) { // noqa: 写失败只损失一次缓存
            warn("SET", key, exception);
        }
    }

    public void evict(String key) {
        if (!properties.enabled()) {
            return;
        }
        try {
            redis.delete(fullKey(key));
        } catch (RuntimeException exception) { // noqa: 删失败靠 TTL 兜底
            warn("DEL", key, exception);
        }
    }

    private Boolean tryLock(String key, String token) {
        try {
            return Boolean.TRUE.equals(redis.opsForValue().setIfAbsent(
                    lockKey(key), token, properties.lockTtl()));
        } catch (RuntimeException exception) { // noqa: 锁不可用时直接回源
            warn("LOCK", key, exception);
            return null;
        }
    }

    private void unlock(String key, String token) {
        try {
            redis.execute(UNLOCK, List.of(lockKey(key)), token);
        } catch (RuntimeException exception) { // noqa: 锁会 TTL 过期
            warn("UNLOCK", key, exception);
        }
    }

    private Duration jittered(Duration ttl) {
        double factor = 1.0 + (ThreadLocalRandom.current().nextDouble() * 2 - 1)
                * properties.jitter();
        return Duration.ofMillis(Math.max(1L, Math.round(ttl.toMillis() * factor)));
    }

    private String fullKey(String key) {
        return properties.keyPrefix() + key;
    }

    private String lockKey(String key) {
        return properties.keyPrefix() + "lock:" + key;
    }

    private <T> CacheResult<T> record(String cacheName, CacheResult<T> result) {
        meterRegistry.counter(
                "cache.requests",
                "cache", cacheName,
                "result", result.outcome().name()).increment();
        return result;
    }

    private void warn(String operation, String key, Exception exception) {
        long now = System.nanoTime();
        long last = lastWarnNanos.get();
        if (now - last < Duration.ofSeconds(30).toNanos()
                || !lastWarnNanos.compareAndSet(last, now)) {
            return;
        }
        log.warn("缓存 {} 失败（回源 DB）：key={} err={}",
                operation, safeKey(key), exception.toString());
    }

    /** 日志里的 key 只留前缀 + 尾段哈希：避免把 user_id 写进日志。 */
    private String safeKey(String key) {
        int index = key.indexOf(':');
        String head = index < 0 ? key : key.substring(0, index);
        String tail = index < 0 ? "" : key.substring(index + 1);
        return head + ":" + Integer.toHexString(tail.hashCode());
    }
}
