package com.marda.business.common;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

/** 每个请求一个 request_id：优先沿用上游 X-Request-Id，否则生成；回写响应头与 MDC。
 *  J8：从 W3C traceparent 解析 trace_id 放 MDC（与网关 / Python 日志同一 id）。 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestIdFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Request-Id";
    public static final String ATTRIBUTE = "marda.request_id";
    public static final String TRACE_ID_MDC = "trace_id";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String requestId = request.getHeader(HEADER);
        if (requestId == null || requestId.isBlank()) {
            requestId = UUID.randomUUID().toString();
        }
        request.setAttribute(ATTRIBUTE, requestId);
        response.setHeader(HEADER, requestId);
        MDC.put("request_id", requestId);
        String traceId = traceIdOf(request.getHeader("traceparent"));
        if (!traceId.isBlank()) {
            MDC.put(TRACE_ID_MDC, traceId);
        }
        try {
            filterChain.doFilter(request, response);
        } finally {
            MDC.remove("request_id");
            MDC.remove(TRACE_ID_MDC);
        }
    }

    /** W3C traceparent（00-&lt;32hex&gt;-&lt;16hex&gt;-&lt;flags&gt;）→ trace-id；格式不对返回空串。 */
    static String traceIdOf(String traceparent) {
        if (traceparent == null) {
            return "";
        }
        String[] parts = traceparent.split("-");
        if (parts.length != 4 || !parts[1].matches("[0-9a-f]{32}")) {
            return "";
        }
        return parts[1];
    }
}
