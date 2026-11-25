package com.marda.business.plan.application;

/** 计划或任务不存在 / 非当前用户（统一 404，不泄露存在性）。 */
public class PlanNotFoundException extends RuntimeException {

    public PlanNotFoundException(String message) {
        super(message);
    }
}
