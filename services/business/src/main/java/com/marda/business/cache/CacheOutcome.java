package com.marda.business.cache;

/** 一次缓存读取的结果；同时用于响应的 X-Cache 头（证据/调试，不是 API 契约）。 */
public enum CacheOutcome {
    HIT,
    MISS,
    /** 缓存被禁用或 Redis 不可用：本次直接回源。 */
    BYPASS
}
