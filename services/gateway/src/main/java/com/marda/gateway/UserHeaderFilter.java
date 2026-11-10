package com.marda.gateway;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.http.HttpHeaders;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.http.server.reactive.ServerHttpRequestDecorator;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.ReactiveSecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.util.UUID;

@Component
public class UserHeaderFilter implements WebFilter, Ordered {

    private final String serviceToken;

    public UserHeaderFilter(@Value("${gateway.service-token:}") String serviceToken) {
        if (serviceToken == null || serviceToken.isBlank()) {
            throw new IllegalStateException("SERVICE_TOKEN must be set");
        }
        this.serviceToken = serviceToken;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        return ReactiveSecurityContextHolder.getContext()
                .map(context -> context.getAuthentication())
                .filter(Authentication::isAuthenticated)
                .filter(JwtAuthenticationToken.class::isInstance)
                .map(UserIdentity::of)
                .defaultIfEmpty("")
                .flatMap(userId -> chain.filter(withHeaders(exchange, userId)));
    }

    private ServerWebExchange withHeaders(ServerWebExchange exchange, String userId) {
        // Security 链路上的 headers 是只读视图；不要原地修改，改用 decorator 提供新的 header 视图。
        HttpHeaders headers = new HttpHeaders();
        headers.putAll(exchange.getRequest().getHeaders());
        // 用户 JWT 只用于 gateway 鉴权，不转给 business；X-User-Id 只信 JWT sub。
        headers.remove("Authorization");
        headers.remove("X-User-Id");
        headers.remove("X-Service-Token");
        if (!userId.isBlank()) {
            headers.set("X-User-Id", userId);
        }
        headers.set("X-Service-Token", serviceToken);
        if (!headers.containsKey("X-Request-Id")) {
            headers.set("X-Request-Id", UUID.randomUUID().toString());
        }
        if (!headers.containsKey("traceparent")) {
            headers.set("traceparent", newTraceparent());
        }
        ServerHttpRequest request = new ServerHttpRequestDecorator(exchange.getRequest()) {
            @Override
            public HttpHeaders getHeaders() {
                return headers;
            }
        };
        return exchange.mutate().request(request).build();
    }

    private String newTraceparent() {
        String traceId = UUID.randomUUID().toString().replace("-", "");
        String spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        return "00-" + traceId + "-" + spanId + "-01";
    }

    @Override
    public int getOrder() {
        return 0;
    }
}
