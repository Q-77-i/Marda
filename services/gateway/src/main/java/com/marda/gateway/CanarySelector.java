package com.marda.gateway;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * 灰度选择：按 user hash 做稳定分桶，同一用户始终落在同一侧。
 * 不用 Spring Cloud Gateway 的 Weight predicate——它是每请求随机，用户会在版本间跳。
 */
final class CanarySelector {

    private CanarySelector() {
    }

    static boolean shouldRouteToCanary(String userId, int percent, String salt) {
        if (userId == null || userId.isBlank()) {
            return false;
        }
        if (percent <= 0) {
            return false;
        }
        if (percent >= 100) {
            return true;
        }
        return bucket(userId, salt) < percent;
    }

    private static int bucket(String userId, String salt) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest((salt + ":" + userId).getBytes(StandardCharsets.UTF_8));
            int value = ((hash[0] & 0xff) << 24)
                    | ((hash[1] & 0xff) << 16)
                    | ((hash[2] & 0xff) << 8)
                    | (hash[3] & 0xff);
            return Math.floorMod(value, 100);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }
}
