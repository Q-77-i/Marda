package com.marda.business.plan.domain;

import org.junit.jupiter.api.Test;

import static com.marda.business.plan.domain.TaskStatus.DONE;
import static com.marda.business.plan.domain.TaskStatus.IN_PROGRESS;
import static com.marda.business.plan.domain.TaskStatus.SKIPPED;
import static com.marda.business.plan.domain.TaskStatus.TODO;
import static org.assertj.core.api.Assertions.assertThat;

class TaskStateMachineTest {

    @Test
    void todo_can_start_finish_or_skip() {
        assertThat(TaskStateMachine.canTransition(TODO, IN_PROGRESS)).isTrue();
        assertThat(TaskStateMachine.canTransition(TODO, DONE)).isTrue();
        assertThat(TaskStateMachine.canTransition(TODO, SKIPPED)).isTrue();
    }

    @Test
    void in_progress_can_finish_or_skip() {
        assertThat(TaskStateMachine.canTransition(IN_PROGRESS, DONE)).isTrue();
        assertThat(TaskStateMachine.canTransition(IN_PROGRESS, SKIPPED)).isTrue();
        assertThat(TaskStateMachine.canTransition(IN_PROGRESS, TODO)).isFalse();
    }

    @Test
    void terminal_states_do_not_reopen() {
        assertThat(TaskStateMachine.canTransition(DONE, IN_PROGRESS)).isFalse();
        assertThat(TaskStateMachine.canTransition(DONE, TODO)).isFalse();
        assertThat(TaskStateMachine.canTransition(SKIPPED, DONE)).isFalse();
    }

    @Test
    void same_status_is_idempotent_noop() {
        for (TaskStatus status : TaskStatus.values()) {
            assertThat(TaskStateMachine.canTransition(status, status)).isTrue();
        }
    }
}
