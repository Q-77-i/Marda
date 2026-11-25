package com.marda.gateway;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * 熔断 / 超时的本地 fallback。SCG 的 CircuitBreaker 过滤器只支持 forward: 形式的 fallbackUri，
 * 这里用普通 WebFlux controller 返回 J1 错误信封。
 */
@RestController
public class FallbackController {

    private final GatewayErrorWriter errorWriter;

    public FallbackController(GatewayErrorWriter errorWriter) {
        this.errorWriter = errorWriter;
    }

    @RequestMapping("/fallback/learning")
    public Mono<ResponseEntity<GatewayErrorWriter.ErrorEnvelope>> learningFallback(
            ServerWebExchange exchange) {
        return Mono.just(ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .contentType(MediaType.APPLICATION_JSON)
                .body(errorWriter.envelope(exchange, GatewayErrorWriter.DEPENDENCY_UNAVAILABLE,
                        "依赖服务暂不可用，请稍后重试")));
    }
}
