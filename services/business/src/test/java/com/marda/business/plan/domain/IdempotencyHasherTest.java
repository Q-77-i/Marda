package com.marda.business.plan.domain;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class IdempotencyHasherTest {

    private final IdempotencyHasher hasher = new IdempotencyHasher(new ObjectMapper());

    @Test
    void same_input_same_hash() {
        assertThat(hasher.hash(input(null, "Agent", List.of(
                new WeakDomain("rag", 2.5, "补检索"),
                new WeakDomain("memory", 3.0, null)))))
                .isEqualTo(hasher.hash(input(null, "Agent", List.of(
                        new WeakDomain("rag", 2.5, "补检索"),
                        new WeakDomain("memory", 3.0, null)))));
    }

    @Test
    void different_goal_different_hash() {
        assertThat(hasher.hash(input("目标 A", "Agent", List.of(
                new WeakDomain("rag", 2.5, null)))))
                .isNotEqualTo(hasher.hash(input("目标 B", "Agent", List.of(
                        new WeakDomain("rag", 2.5, null)))));
    }

    @Test
    void reordered_weak_domains_different_hash_because_priority_changes() {
        assertThat(hasher.hash(input(null, "Agent", List.of(
                new WeakDomain("rag", 2.0, null),
                new WeakDomain("memory", 3.0, null)))))
                .isNotEqualTo(hasher.hash(input(null, "Agent", List.of(
                        new WeakDomain("memory", 3.0, null),
                        new WeakDomain("rag", 2.0, null)))));
    }

    @Test
    void null_and_blank_text_normalize_to_the_same_hash() {
        assertThat(hasher.hash(input(null, "Agent", List.of(
                new WeakDomain("rag", 2.5, null)))))
                .isEqualTo(hasher.hash(input(" ", " Agent ", List.of(
                        new WeakDomain("rag", 2.5, " ")))));
    }

    private static PlanInput input(String goal, String position, List<WeakDomain> weakDomains) {
        return new PlanInput(
                "u1", "iv1", goal, position, null,
                new ReportSummary(3.4, weakDomains));
    }
}
