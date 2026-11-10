package com.marda.gateway;

import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;

/**
 * 用户身份的单一来源：JWT 的 sub 优先，其他认证类型退回 Authentication#getName。
 * X-User-Id、限流 key、灰度分桶都必须走这里，避免 claim 一变就分叉。
 */
final class UserIdentity {

    private UserIdentity() {
    }

    static String of(Authentication authentication) {
        if (authentication instanceof JwtAuthenticationToken jwt) {
            String subject = jwt.getToken().getSubject();
            if (subject != null && !subject.isBlank()) {
                return subject;
            }
        }
        return authentication.getName();
    }
}
