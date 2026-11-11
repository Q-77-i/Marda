package com.marda.business.plan.domain;

/** 任务状态机：只允许向前推进；同状态视为幂等 no-op；终态不可回退。 */
public final class TaskStateMachine {

    private TaskStateMachine() {
    }

    public static boolean canTransition(TaskStatus from, TaskStatus to) {
        if (from == null || to == null) {
            return false;
        }
        if (from == to) {
            return true;
        }
        return switch (from) {
            case TODO -> to == TaskStatus.IN_PROGRESS || to == TaskStatus.DONE || to == TaskStatus.SKIPPED;
            case IN_PROGRESS -> to == TaskStatus.DONE || to == TaskStatus.SKIPPED;
            case DONE, SKIPPED -> false;
        };
    }
}
