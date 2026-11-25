package com.marda.gateway;

import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RateLimiterConfig {

    /**
     * 用带默认值的构造器建 bean：Spring Cloud Gateway 自动配置的
     * {@code redisRateLimiter(redisTemplate, script, configurationService)} 不设置 defaultConfig，
     * 只有 2/3 参数 int 构造器才会把桶参数写进默认配置。
     */
    @Bean
    RedisRateLimiter redisRateLimiter(GatewayRateLimitProperties properties) {
        return new RedisRateLimiter(properties.getReplenishRate(),
                properties.getBurstCapacity(),
                properties.getRequestedTokens());
    }
}
