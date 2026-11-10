package com.marda.gateway;

import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RateLimiter;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.cloud.gateway.route.Route;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.ReactiveSecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.net.InetSocketAddress;
import java.util.Map;

/**
 * Redis 令牌桶限流。
 *
 * <p>复用 Spring Cloud Gateway 的 {@link RedisRateLimiter}（成熟 Lua + token bucket），
 * 但不用内置 {@code RequestRateLimiter} 过滤器——它在拒绝时只写状态码，不写错误信封、
 * 也不写 Retry-After。这里自己接管拒绝响应。
 *
 * <p>失败语义：Redis 超时 / 异常一律放行（限流是保护，不是正确性）。
 */
@Component
public class TokenBucketGatewayFilterFactory extends AbstractGatewayFilterFactory<TokenBucketGatewayFilterFactory.Config> {

    private static final Logger log = LoggerFactory.getLogger(TokenBucketGatewayFilterFactory.class);

    private final RedisRateLimiter rateLimiter;
    private final GatewayRateLimitProperties properties;
    private final GatewayErrorWriter errorWriter;
    private final MeterRegistry meterRegistry;
    private final KeyResolver keyResolver;

    public TokenBucketGatewayFilterFactory(RedisRateLimiter rateLimiter,
                                           GatewayRateLimitProperties properties,
                                           GatewayErrorWriter errorWriter,
                                           MeterRegistry meterRegistry) {
        super(Config.class);
        this.rateLimiter = rateLimiter;
        this.properties = properties;
        this.errorWriter = errorWriter;
        this.meterRegistry = meterRegistry;
        this.keyResolver = exchange -> ReactiveSecurityContextHolder.getContext()
                .map(context -> context.getAuthentication())
                .filter(Authentication::isAuthenticated)
                .map(UserIdentity::of)
                .filter(name -> !name.isBlank() && !"anonymousUser".equals(name))
                .map(name -> "user:" + name)
                .switchIfEmpty(Mono.fromSupplier(() -> clientKey(exchange)));
    }

    @Override
    public GatewayFilter apply(Config config) {
        return (exchange, chain) -> {
            if (!properties.isEnabled()) {
                return chain.filter(exchange);
            }
            String routeId = routeId(exchange);
            return Mono.defer(() -> keyResolver.resolve(exchange))
                    .timeout(properties.getKeyResolveTimeout())
                    .onErrorResume(error -> Mono.just(clientKey(exchange)))
                    .defaultIfEmpty(clientKey(exchange))
                    .flatMap(key -> Mono.defer(() -> rateLimiter.isAllowed(routeId, key))
                            .timeout(properties.getRedisTimeout())
                            .onErrorResume(error -> {
                                log.debug("限流计数超时（fail open）：{}", error.toString());
                                // RedisRateLimiter 自身也会吞掉部分 Redis 异常并放行；
                                // 这个计数器只覆盖我们这一层的超时/异常。
                                meterRegistry.counter("gateway.rate_limit.redis_timeout", "route", routeId)
                                        .increment();
                                return Mono.just(new RateLimiter.Response(true, Map.of()));
                            })
                            .flatMap(response -> {
                                response.getHeaders().forEach((name, value) ->
                                        exchange.getResponse().getHeaders().set(name, value));
                                if (response.isAllowed()) {
                                    return chain.filter(exchange);
                                }
                                meterRegistry.counter("gateway.rate_limit.rejected", "route", routeId)
                                        .increment();
                                return errorWriter.write(exchange, HttpStatus.TOO_MANY_REQUESTS,
                                        GatewayErrorWriter.RATE_LIMITED, "请求过于频繁，请稍后重试",
                                        properties.getRetryAfterSeconds());
                            }));
        };
    }

    private String routeId(ServerWebExchange exchange) {
        Route route = exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR);
        return route == null ? "unknown" : route.getId();
    }

    private String clientKey(ServerWebExchange exchange) {
        if (properties.isTrustForwardedFor()) {
            String forwarded = exchange.getRequest().getHeaders().getFirst("X-Forwarded-For");
            if (forwarded != null && !forwarded.isBlank()) {
                return "ip:" + forwarded.split(",")[0].trim();
            }
        }
        InetSocketAddress remote = exchange.getRequest().getRemoteAddress();
        if (remote == null) {
            return "ip:unknown";
        }
        return "ip:" + (remote.getAddress() == null
                ? remote.getHostString()
                : remote.getAddress().getHostAddress());
    }

    public static class Config {
    }
}
