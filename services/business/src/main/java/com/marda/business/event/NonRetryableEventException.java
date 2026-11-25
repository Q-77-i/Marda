package com.marda.business.event;

/** 消息结构 / 版本 / 载荷校验失败：**不重试**，直接进 DLQ（毒消息）。 */
public class NonRetryableEventException extends RuntimeException {

    public NonRetryableEventException(String message) {
        super(message);
    }

    public NonRetryableEventException(String message, Throwable cause) {
        super(message, cause);
    }
}
