package com.marda.business.cache;

import java.time.LocalDate;
import java.util.UUID;

/** 学习计划的缓存键（统一在这里拼，避免各处 key 格式漂移）。 */
public final class CacheKeys {

    private CacheKeys() {
    }

    public static String today(String userId, LocalDate date) {
        return "learning:today:" + userId + ":" + date;
    }

    public static String plan(String userId, UUID planId) {
        return "learning:plan:" + userId + ":" + planId;
    }
}
