package com.marda.business.plan.domain;

/** 建计划输入不合法（Web 层映射为 422）。 */
public class InvalidPlanInputException extends RuntimeException {

    public InvalidPlanInputException(String message) {
        super(message);
    }
}
