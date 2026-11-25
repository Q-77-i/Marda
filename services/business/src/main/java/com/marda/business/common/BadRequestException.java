package com.marda.business.common;

/** 请求参数不合法（Web 层映射为 400）。 */
public class BadRequestException extends RuntimeException {

    public BadRequestException(String message) {
        super(message);
    }
}
