package com.marda.gateway;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.cloud.gateway.route.Route;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.core.Ordered;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.ReactiveSecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.util.UriComponentsBuilder;
import reactor.core.publisher.Mono;

import java.net.URI;

/**
 * 灰度路由：按 user hash 选择稳定 / 灰度实例。
 *
 * <p>必须是 GlobalFilter 且 order 大于 {@code RouteToRequestUrlFilter}（10000）：
 * 路由过滤器先跑，RouteToRequestUrlFilter 之后才把最终 URL 放进
 * {@code GATEWAY_REQUEST_URL_ATTR}；如果在路由过滤器里改写，会被它覆盖。
 * 这个 order 仍在 {@code NettyRoutingFilter}（LOWEST_PRECEDENCE）之前。
 */
@Component
public class CanaryGlobalFilter implements GlobalFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(CanaryGlobalFilter.class);
    private static final int ORDER = 10_100;

    private final GatewayCanaryProperties properties;
    private final String serviceToken;

    public CanaryGlobalFilter(GatewayCanaryProperties properties,
                              @Value("${gateway.service-token:}") String serviceToken) {
        this.properties = properties;
        this.serviceToken = serviceToken;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        Route route = exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR);
        if (!properties.isEnabled() || route == null
                || !properties.getRouteId().equals(route.getId())) {
            return chain.filter(exchange);
        }
        String override = exchange.getRequest().getHeaders().getFirst(properties.getHeaderName());
        return ReactiveSecurityContextHolder.getContext()
                .map(context -> context.getAuthentication())
                .filter(Authentication::isAuthenticated)
                .map(UserIdentity::of)
                .defaultIfEmpty("")
                .flatMap(userId -> route(exchange, chain, userId, override));
    }

    private Mono<Void> route(ServerWebExchange exchange, GatewayFilterChain chain,
                             String userId, String override) {
        boolean stableOverride = overrideEnabled(exchange, override, "stable");
        boolean canaryOverride = overrideEnabled(exchange, override, "canary");
        boolean toCanary = canaryOverride
                || (!stableOverride
                && CanarySelector.shouldRouteToCanary(userId, properties.getPercent(), properties.getSalt()));
        if (!toCanary || properties.getCanaryUri() == null || properties.getCanaryUri().isBlank()) {
            return chain.filter(exchange);
        }
        URI current = exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_REQUEST_URL_ATTR);
        if (current == null) {
            return chain.filter(exchange);
        }
        try {
            UriComponentsBuilder builder = UriComponentsBuilder.fromUri(URI.create(properties.getCanaryUri()))
                    .path(current.getRawPath() == null ? "/" : current.getRawPath());
            if (current.getRawQuery() != null) {
                builder.query(current.getRawQuery());
            }
            URI target = builder.build(true).toUri();
            exchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_REQUEST_URL_ATTR, target);
        } catch (IllegalArgumentException e) {
            // 灰度地址配坏时回退稳定版，不能把主路径打挂。
            log.warn("灰度地址不可用，回退稳定版：{}", e.getMessage());
        }
        return chain.filter(exchange);
    }

    private boolean overrideEnabled(ServerWebExchange exchange, String override, String value) {
        if (!properties.isHeaderEnabled() || override == null || !value.equalsIgnoreCase(override.trim())) {
            return false;
        }
        // “内部可用”不能只靠一个布尔开关：必须同时带 X-Canary-Token（= SERVICE_TOKEN），
        // 外部客户端拿不到这个值，不能自己给自己灰度。
        String token = exchange.getRequest().getHeaders().getFirst("X-Canary-Token");
        return serviceToken != null && !serviceToken.isBlank() && serviceToken.equals(token);
    }

    @Override
    public int getOrder() {
        return ORDER;
    }
}
