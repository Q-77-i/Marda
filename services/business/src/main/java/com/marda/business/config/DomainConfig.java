package com.marda.business.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.marda.business.plan.domain.IdempotencyHasher;
import com.marda.business.plan.domain.PlanFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;
import java.time.ZoneId;

/** 时间与纯函数 bean：固定 Clock / 时区便于测试与“今日”口径统一。 */
@Configuration
public class DomainConfig {

    @Bean
    public Clock clock() {
        return Clock.systemUTC();
    }

    @Bean
    public ZoneId businessZone(@Value("${business.timezone:Asia/Shanghai}") String timezone) {
        return ZoneId.of(timezone);
    }

    @Bean
    public PlanFactory planFactory(Clock clock, ZoneId businessZone) {
        return new PlanFactory(clock, businessZone);
    }

    @Bean
    public IdempotencyHasher idempotencyHasher(ObjectMapper objectMapper) {
        return new IdempotencyHasher(objectMapper);
    }
}
