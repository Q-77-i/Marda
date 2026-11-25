package com.marda.business.plan.domain;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PlanFactoryTest {

    private static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");
    private static final Clock CLOCK = Clock.fixed(
            Instant.parse("2026-11-11T02:00:00Z"), ZoneOffset.UTC);

    private final PlanFactory factory = new PlanFactory(CLOCK, ZONE);

    @Test
    void creates_one_task_per_weak_domain_sorted_by_score() {
        PlanInput input = new PlanInput(
                "u1", "iv1", null, "Agent", "tech",
                new ReportSummary(3.4, List.of(
                        new WeakDomain("rag", 2.5, "补混合检索"),
                        new WeakDomain("memory", 3.0, "补压缩策略"),
                        new WeakDomain("tool-use", 2.0, " ")
                )));

        PlanDecision decision = factory.decide(input);

        assertThat(decision).isInstanceOf(PlanDecision.Create.class);
        PlanDraft draft = ((PlanDecision.Create) decision).draft();
        assertThat(draft.goal()).isEqualTo("针对「Agent」的面试短板学习计划");
        assertThat(draft.tasks()).extracting(TaskDraft::domain)
                .containsExactly("tool-use", "rag", "memory");
        assertThat(draft.tasks()).extracting(TaskDraft::priority)
                .containsExactly(1, 2, 3);
        assertThat(draft.tasks()).extracting(TaskDraft::title)
                .containsExactly("针对性补强", "补混合检索", "补压缩策略");
        assertThat(draft.tasks()).extracting(TaskDraft::dueAt).containsExactly(
                Instant.parse("2026-11-11T15:59:59Z"),
                Instant.parse("2026-11-12T15:59:59Z"),
                Instant.parse("2026-11-13T15:59:59Z"));
    }

    @Test
    void empty_weak_domains_skip_with_reason() {
        PlanDecision decision = factory.decide(input(new ReportSummary(0.0, List.of())));

        assertThat(decision).isEqualTo(new PlanDecision.Skip(SkipReason.NO_WEAK_DOMAINS));
    }

    @Test
    void behavioral_interview_skips_even_with_weak_domains() {
        PlanInput input = new PlanInput(
                "u1", "iv1", null, "Agent", "behavioral",
                new ReportSummary(3.0, List.of(new WeakDomain("rag", 2.0, "补检索"))));

        assertThat(factory.decide(input))
                .isEqualTo(new PlanDecision.Skip(SkipReason.UNSUPPORTED_INTERVIEW_TYPE));
    }

    @Test
    void duplicate_domains_are_deduped_keeping_first_occurrence() {
        PlanInput input = input(new ReportSummary(3.0, List.of(
                new WeakDomain("rag", 2.0, "第一条"),
                new WeakDomain("memory", 3.0, "记忆"),
                new WeakDomain("rag", 4.0, "第二条"))));

        PlanDraft draft = ((PlanDecision.Create) factory.decide(input)).draft();

        assertThat(draft.tasks()).extracting(TaskDraft::domain)
                .containsExactly("rag", "memory");
        assertThat(draft.tasks()).extracting(TaskDraft::title)
                .containsExactly("第一条", "记忆");
    }

    @Test
    void more_than_five_domains_is_rejected() {
        PlanInput input = input(new ReportSummary(3.0, List.of(
                new WeakDomain("a", 2.0, null),
                new WeakDomain("b", 2.1, null),
                new WeakDomain("c", 2.2, null),
                new WeakDomain("d", 2.3, null),
                new WeakDomain("e", 2.4, null),
                new WeakDomain("f", 2.5, null))));

        assertThatThrownBy(() -> factory.decide(input))
                .isInstanceOf(InvalidPlanInputException.class)
                .hasMessageContaining("最多 5 个");
    }

    @Test
    void explicit_goal_wins_over_position() {
        PlanInput input = new PlanInput(
                "u1", "iv1", "自定义目标", "Agent", "tech",
                new ReportSummary(3.0, List.of(new WeakDomain("rag", 2.0, null))));

        PlanDraft draft = ((PlanDecision.Create) factory.decide(input)).draft();

        assertThat(draft.goal()).isEqualTo("自定义目标");
    }

    @Test
    void missing_goal_and_position_uses_generic_goal() {
        PlanInput input = new PlanInput(
                "u1", "iv1", " ", null, null,
                new ReportSummary(3.0, List.of(new WeakDomain("rag", 2.0, null))));

        PlanDraft draft = ((PlanDecision.Create) factory.decide(input)).draft();

        assertThat(draft.goal()).isEqualTo("面试短板补强计划");
    }

    @Test
    void blank_domain_is_rejected() {
        PlanInput input = input(new ReportSummary(3.0, List.of(
                new WeakDomain(" ", 2.0, null))));

        assertThatThrownBy(() -> factory.decide(input))
                .isInstanceOf(InvalidPlanInputException.class)
                .hasMessageContaining("空 domain");
    }

    @Test
    void non_finite_score_is_rejected() {
        PlanInput input = input(new ReportSummary(3.0, List.of(
                new WeakDomain("rag", Double.NaN, null))));

        assertThatThrownBy(() -> factory.decide(input))
                .isInstanceOf(InvalidPlanInputException.class)
                .hasMessageContaining("0–5");
    }

    private static PlanInput input(ReportSummary summary) {
        return new PlanInput("u1", "iv1", null, "Agent", "tech", summary);
    }
}
