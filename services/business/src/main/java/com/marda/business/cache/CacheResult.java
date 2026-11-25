package com.marda.business.cache;

/** 缓存读取结果：value 可能为 null（空值缓存命中），outcome 说明来源。 */
public record CacheResult<T>(T value, CacheOutcome outcome) {

    public boolean hit() {
        return outcome == CacheOutcome.HIT;
    }
}
