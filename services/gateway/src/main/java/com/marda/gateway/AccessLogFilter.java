package com.marda.gateway;

import net.logstash.logback.argument.StructuredArguments;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.core.Ordered;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;

@Component
public class AccessLogFilter implements WebFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AccessLogFilter.class);

    private final ExecutorService executor;
    private final ObjectProvider<Tracer> tracerProvider;
    /** ha profile 用 LogstashEncoder（结构化字段）；默认 console pattern 只认 {}。 */
    private final boolean jsonLogging;

    public AccessLogFilter(ExecutorService gatewayBlockingExecutor,
                           ObjectProvider<Tracer> tracerProvider,
                           Environment environment) {
        this.executor = gatewayBlockingExecutor;
        this.tracerProvider = tracerProvider;
        this.jsonLogging = Arrays.stream(environment.getActiveProfiles()).anyMatch("ha"::equals);
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
                        if (jsonLogging) {
                            log.info("access", StructuredArguments.entries(fields));
                        } else {
                            // 默认 console pattern 不渲染 StructuredArguments；不分支的话
                            // 单机形态（无 ha profile）的访问日志只剩一个词 access。
                            log.info(
                                    "access method={} path={} status={} duration_ms={} request_id={} trace_id={}",
                                    fields.get("method"), fields.get("path"), fields.get("status"),
                                    fields.get("duration_ms"), fields.get("request_id"),
                                    fields.get("trace_id"));
                        }
                    });
                } catch (RuntimeException ignored) {
                    // 应用关闭时 executor 可能已关闭；访问日志不能影响响应。
                }
            });
        });
    }

    @Override
    public int getOrder() {
        // 在 Spring Security（WebFilterChainProxy order = -100）之前：未认证请求也会被
        // 记录，且 X-Request-Id 对 401/403 也会回写（审查实测：LOWEST 时 401 完全不可见）。
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
