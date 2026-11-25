package com.marda.gateway;

import net.logstash.logback.argument.StructuredArguments;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;

@Component
public class AccessLogFilter implements WebFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AccessLogFilter.class);

    private final ExecutorService executor;
    private final ObjectProvider<Tracer> tracerProvider;

    public AccessLogFilter(ExecutorService gatewayBlockingExecutor,
                           ObjectProvider<Tracer> tracerProvider) {
        this.executor = gatewayBlockingExecutor;
        this.tracerProvider = tracerProvider;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        long started = System.nanoTime();
        // J8：request_id 在最外层生成并写进请求头，下游（business / 日志）复用同一个值。
        String headerRequestId = exchange.getRequest().getHeaders().getFirst("X-Request-Id");
        String requestId = (headerRequestId == null || headerRequestId.isBlank())
                ? UUID.randomUUID().toString()
                : headerRequestId;
        ServerWebExchange mutated = exchange.mutate()
                .request(exchange.getRequest().mutate()
                        .headers(headers -> headers.set("X-Request-Id", requestId))
                        .build())
                .build();
        mutated.getResponse().getHeaders().set("X-Request-Id", requestId);
        final String finalRequestId = requestId;
        // J8-B：trace_id 从 OTel 的当前 span（Reactor 上下文）取，与 Jaeger 同源；
        // traceparent 由 client observation 注入，不在这里手写。
        return Mono.deferContextual(context -> {
            // 优先 Micrometer 的当前 span（Spring Boot 的 Reactor 传播），
            // 再 MDC，最后读 Reactor 上下文里的 Span；都拿不到就留空。
            String traceId = null;
            Tracer tracer = tracerProvider.getIfAvailable();
            if (tracer != null && tracer.currentSpan() != null) {
                traceId = tracer.currentSpan().context().traceId();
            }
            if (traceId == null || traceId.isBlank()) {
                traceId = MDC.get("traceId");
            }
            if (traceId == null || traceId.isBlank()) {
                Span span = context.getOrDefault(Span.class, null);
                traceId = span == null ? "" : span.context().traceId();
            }
            final String finalTraceId = traceId == null ? "" : traceId;
            return chain.filter(mutated).doFinally(signal -> {
                long durationMs = (System.nanoTime() - started) / 1_000_000;
                String status = mutated.getResponse().getStatusCode() == null
                        ? "none"
                        : String.valueOf(mutated.getResponse().getStatusCode().value());
                try {
                    // 日志写盘 / stdout 是阻塞 I/O，放虚拟线程上，不占 Netty 事件循环。
                    executor.submit(() -> {
                        Map<String, Object> fields = new LinkedHashMap<>();
                        fields.put("method", mutated.getRequest().getMethod().name());
                        fields.put("path", mutated.getRequest().getPath().value());
                        fields.put("status", status);
                        fields.put("duration_ms", durationMs);
                        fields.put("request_id", finalRequestId);
                        fields.put("trace_id", finalTraceId);
                        fields.put("virtual", Thread.currentThread().isVirtual());
                        log.info("access", StructuredArguments.entries(fields));
                    });
                } catch (RuntimeException ignored) {
                    // 应用关闭时 executor 可能已关闭；访问日志不能影响响应。
                }
            });
        });
    }

    @Override
    public int getOrder() {
        return Ordered.LOWEST_PRECEDENCE;
    }
}
