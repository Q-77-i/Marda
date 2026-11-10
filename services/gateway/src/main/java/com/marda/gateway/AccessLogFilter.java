package com.marda.gateway;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.util.concurrent.ExecutorService;

@Component
public class AccessLogFilter implements WebFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AccessLogFilter.class);

    private final ExecutorService executor;

    public AccessLogFilter(ExecutorService gatewayBlockingExecutor) {
        this.executor = gatewayBlockingExecutor;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        long started = System.nanoTime();
        return chain.filter(exchange).doFinally(signal -> {
            long durationMs = (System.nanoTime() - started) / 1_000_000;
            String method = exchange.getRequest().getMethod().name();
            String path = exchange.getRequest().getPath().value();
            String status = exchange.getResponse().getStatusCode() == null
                    ? "none"
                    : String.valueOf(exchange.getResponse().getStatusCode().value());
            String requestId = exchange.getRequest().getHeaders().getFirst("X-Request-Id");
            try {
                // 日志写盘 / stdout 是阻塞 I/O，放虚拟线程上，不占 Netty 事件循环。
                executor.submit(() -> log.info(
                        "access method={} path={} status={} duration_ms={} request_id={} virtual={}",
                        method, path, status, durationMs, requestId,
                        Thread.currentThread().isVirtual()));
            } catch (RuntimeException ignored) {
                // 应用关闭时 executor 可能已关闭；访问日志不能影响响应。
            }
        });
    }

    @Override
    public int getOrder() {
        return Ordered.LOWEST_PRECEDENCE;
    }
}
