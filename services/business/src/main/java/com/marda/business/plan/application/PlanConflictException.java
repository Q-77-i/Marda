package com.marda.business.plan.application;

import java.util.Map;

/** 幂等 / 版本 / 状态冲突（Web 层映射为 409，details 带当前值）。 */
public class PlanConflictException extends RuntimeException {

    private final String code;
    private final Map<String, Object> details;

    public PlanConflictException(String code, String message, Map<String, Object> details) {
        super(message);
        this.code = code;
        this.details = details == null ? Map.of() : Map.copyOf(details);
    }

    public String code() {
        return code;
    }

    public Map<String, Object> details() {
        return details;
    }
}
