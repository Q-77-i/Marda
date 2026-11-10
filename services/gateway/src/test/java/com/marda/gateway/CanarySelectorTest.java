package com.marda.gateway;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class CanarySelectorTest {

    @Test
    void percent_zero_never_routes_to_canary() {
        assertThat(CanarySelector.shouldRouteToCanary("u_1", 0, "salt")).isFalse();
    }

    @Test
    void percent_100_always_routes_to_canary() {
        assertThat(CanarySelector.shouldRouteToCanary("u_1", 100, "salt")).isTrue();
    }

    @Test
    void same_user_and_salt_is_deterministic() {
        boolean first = CanarySelector.shouldRouteToCanary("u_sticky", 50, "salt");
        for (int i = 0; i < 1000; i++) {
            assertThat(CanarySelector.shouldRouteToCanary("u_sticky", 50, "salt"))
                    .isEqualTo(first);
        }
    }

    @Test
    void blank_user_goes_stable() {
        assertThat(CanarySelector.shouldRouteToCanary("", 100, "salt")).isFalse();
        assertThat(CanarySelector.shouldRouteToCanary(null, 100, "salt")).isFalse();
    }

    @Test
    void fifty_percent_is_roughly_balanced_over_many_users() {
        int canary = 0;
        for (int i = 0; i < 10_000; i++) {
            if (CanarySelector.shouldRouteToCanary("u_" + i, 50, "marda-gateway")) {
                canary++;
            }
        }
        assertThat(canary).isBetween(4_000, 6_000);
    }
}
