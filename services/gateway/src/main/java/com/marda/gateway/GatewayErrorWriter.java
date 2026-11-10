package com.marda.gateway;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * 网关层错误信封（J1 契约）：{code, message, request_id}。
 * 限流 / 舱壁 / 熔断 fallback 共用同一份信封形状与错误码，避免各写一套 JSON。
 */
@Component
public class GatewayErrorWriter {

    public static final String RATE_LIMITED = "RATE_LIMITED";
    public static final String DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE";

    private final ObjectMapper objectMapper;

    public GatewayErrorWriter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public ErrorEnvelope envelope(ServerWebExchange exchange, String code, String message) {
        return new ErrorEnvelope(code, message,
                exchange.getRequest().getHeaders().getFirst("X-Request-Id"));
    }

    public Mono<Void> write(ServerWebExchange exchange, HttpStatus status, String code,
                            String message, Integer retryAfterSeconds) {
        ServerHttpResponse response = exchange.getResponse();
        if (response.isCommitted()) {
            return Mono.empty();
        }
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        if (retryAfterSeconds != null) {
            response.getHeaders().set(HttpHeaders.RETRY_AFTER, String.valueOf(retryAfterSeconds));
        }
        byte[] body;
        try {
            body = objectMapper.writeValueAsBytes(envelope(exchange, code, message));
        } catch (JsonProcessingException e) {
            return Mono.error(e);
        }
        response.getHeaders().setContentLength(body.length);
        return response.writeWith(Mono.just(response.bufferFactory().wrap(body)));
    }

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ErrorEnvelope(String code, String message,
                                @JsonProperty("request_id") String requestId) {
    }
}
