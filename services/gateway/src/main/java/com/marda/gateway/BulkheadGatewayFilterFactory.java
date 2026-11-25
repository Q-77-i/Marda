package com.marda.gateway;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

import java.time.Duration;

/**
 * 舱壁：限制单个网关实例同时打到下游的请求数。
 *
 * <p>用 {@link Bulkhead#tryAcquirePermission()}（不等待）而不是 acquirePermission，
 * 避免在 Netty 事件循环上阻塞。拒绝属于本地降载，返回 429；它排在熔断器外层，
 * 因此不会把熔断器记成失败（否则越忙越容易把下游一起熔断）。
 */
@Component
public class BulkheadGatewayFilterFactory extends AbstractGatewayFilterFactory<BulkheadGatewayFilterFactory.Config> {

    private final BulkheadRegistry bulkheadRegistry;
    private final GatewayResilienceProperties properties;
    private final GatewayErrorWriter errorWriter;
    private final MeterRegistry meterRegistry;

    public BulkheadGatewayFilterFactory(BulkheadRegistry bulkheadRegistry,
                                        GatewayResilienceProperties properties,
                                        GatewayErrorWriter errorWriter,
                                        MeterRegistry meterRegistry) {
        super(Config.class);
        this.bulkheadRegistry = bulkheadRegistry;
        this.properties = properties;
        this.errorWriter = errorWriter;
        this.meterRegistry = meterRegistry;
    }

    @Override
    public GatewayFilter apply(Config config) {
        if (!properties.isBulkheadEnabled()) {
            return (exchange, chain) -> chain.filter(exchange);
        }
        String name = config.getName() == null || config.getName().isBlank()
                ? "learning"
                : config.getName();
        Bulkhead bulkhead = bulkheadRegistry.bulkhead(name, BulkheadConfig.custom()
                .maxConcurrentCalls(properties.getBulkheadMaxConcurrentCalls())
                .maxWaitDuration(Duration.ZERO)
                .build());
        return (exchange, chain) -> {
            if (!bulkhead.tryAcquirePermission()) {
                meterRegistry.counter("gateway.bulkhead.rejected", "route", name).increment();
                return errorWriter.write(exchange, HttpStatus.TOO_MANY_REQUESTS,
                        GatewayErrorWriter.RATE_LIMITED, "网关繁忙，请稍后重试", 1);
            }
            return Mono.defer(() -> chain.filter(exchange))
                    .doFinally(signal -> bulkhead.onComplete());
        };
    }

    public static class Config {
        private String name;

        public String getName() {
            return name;
        }

        public void setName(String name) {
            this.name = name;
        }
    }
}
