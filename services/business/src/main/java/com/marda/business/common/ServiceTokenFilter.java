package com.marda.business.common;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.AntPathMatcher;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

/**
 * 内网服务令牌校验：业务服务只信任网关 / 内部调用方。
 *
 * <p>`/api/v1/**` 还要求 X-User-Id；内部 from-report 的用户头与 payload 交叉校验在 controller。
 * 错误信封与 controller 一致，request_id 来自 RequestIdFilter。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class ServiceTokenFilter extends OncePerRequestFilter {

    public static final String SERVICE_TOKEN_HEADER = "X-Service-Token";
    public static final String USER_ID_HEADER = "X-User-Id";

    private final byte[] expectedToken;
    private final List<String> permitPatterns;
    private final AntPathMatcher pathMatcher = new AntPathMatcher();
    private final ObjectMapper objectMapper;

    public ServiceTokenFilter(
            @Value("${business.service-token:}") String serviceToken,
            @Value("${business.security.permit-paths:/actuator/health,/actuator/health/**,"
                    + "/actuator/info,/actuator/prometheus,/error}") String permitPaths,
            ObjectMapper objectMapper) {
        if (serviceToken == null || serviceToken.isBlank()) {
            throw new IllegalStateException("SERVICE_TOKEN must be set");
        }
        this.expectedToken = serviceToken.getBytes(StandardCharsets.UTF_8);
        this.permitPatterns = Arrays.stream(permitPaths.split(","))
                .map(String::trim)
                .filter(path -> !path.isEmpty())
                .toList();
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String path = request.getRequestURI();
        if (isPermitted(path)) {
            filterChain.doFilter(request, response);
            return;
        }

        String provided = request.getHeader(SERVICE_TOKEN_HEADER);
        if (provided == null
                || !MessageDigest.isEqual(expectedToken, provided.getBytes(StandardCharsets.UTF_8))) {
            writeError(request, response, HttpServletResponse.SC_FORBIDDEN,
                    "FORBIDDEN", "服务令牌无效");
            return;
        }
        if (path.startsWith("/api/v1/")) {
            String userId = request.getHeader(USER_ID_HEADER);
            if (userId == null || userId.isBlank()) {
                writeError(request, response, HttpServletResponse.SC_UNAUTHORIZED,
                        "UNAUTHORIZED", "未认证：缺少用户身份");
                return;
            }
        }
        filterChain.doFilter(request, response);
    }

    private boolean isPermitted(String path) {
        return permitPatterns.stream().anyMatch(pattern -> pathMatcher.match(pattern, path));
    }

    private void writeError(HttpServletRequest request, HttpServletResponse response,
                            int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        String requestId = (String) request.getAttribute(RequestIdFilter.ATTRIBUTE);
        objectMapper.writeValue(response.getWriter(),
                new ApiError(code, message, requestId, Map.of()));
    }
}
