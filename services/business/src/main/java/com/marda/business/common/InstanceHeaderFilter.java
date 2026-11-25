package com.marda.business.common;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * J6-A：响应头带实例标识（hostname:pid），用于多实例 / 摘流演练的证据。
 *
 * <p>诊断信息，不是 API 契约；BUSINESS_INSTANCE_HEADER_ENABLED=false 可关闭。
 */
@Component
@ConditionalOnProperty(
        name = "business.instance-header-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class InstanceHeaderFilter extends OncePerRequestFilter {

    static final String HEADER = "X-Marda-Instance";

    private final String instance;

    public InstanceHeaderFilter() {
        String host = System.getenv().getOrDefault("HOSTNAME", "local");
        this.instance = host + ":" + ProcessHandle.current().pid();
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        response.setHeader(HEADER, instance);
        filterChain.doFilter(request, response);
    }
}
