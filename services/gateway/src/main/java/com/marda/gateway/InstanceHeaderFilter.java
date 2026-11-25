package com.marda.gateway;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * J6-A：在响应头带实例标识（hostname:pid），用于多实例演练的证据。
 *
 * <p>它不是 API 契约，只是诊断信息；GATEWAY_INSTANCE_HEADER_ENABLED=false 可关闭。
 * 在过滤器链最前面设置响应头，成功与降级响应都能带上。
 */
@Component
public class InstanceHeaderFilter implements GlobalFilter, Ordered {

    static final String HEADER = "X-Marda-Instance";

    private final boolean enabled;
    private final String instance;

    public InstanceHeaderFilter(
            @Value("${gateway.instance-header.enabled:true}") boolean enabled) {
        this.enabled = enabled;
        String host = System.getenv().getOrDefault("HOSTNAME", "local");
        this.instance = host + ":" + ProcessHandle.current().pid();
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        if (enabled) {
            exchange.getResponse().getHeaders().set(HEADER, instance);
        }
        return chain.filter(exchange);
    }

    @Override
    public int getOrder() {
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
