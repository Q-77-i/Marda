package com.marda.business.common;

import java.util.Map;

/** 统一错误信封：`{code, message, request_id, details?}`。 */
public record ApiError(String code, String message, String requestId, Map<String, Object> details) {
}
