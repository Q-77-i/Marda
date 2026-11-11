package com.marda.business.plan.domain;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;

/** 计划生成规则（纯函数，见私有规划 §3.3）。 */
public final class PlanFactory {

    static final int MAX_TASKS = 5;
    private static final String DEFAULT_TASK_TITLE = "针对性补强";

    private final Clock clock;
    private final ZoneId zone;

    public PlanFactory(Clock clock, ZoneId zone) {
        this.clock = clock;
        this.zone = zone;
    }

    public PlanDecision decide(PlanInput input) {
        if (input == null || input.reportSummary() == null) {
            throw new InvalidPlanInputException("report_summary 不能为空");
        }
        String interviewType = input.interviewType() == null ? "tech" : input.interviewType();
        if (!"tech".equals(interviewType)) {
            return new PlanDecision.Skip(SkipReason.UNSUPPORTED_INTERVIEW_TYPE);
        }
        List<WeakDomain> weakDomains = input.reportSummary().weakDomains();
        if (weakDomains == null || weakDomains.isEmpty()) {
            return new PlanDecision.Skip(SkipReason.NO_WEAK_DOMAINS);
        }

        // 同一个域在报告里重复出现只建一个任务；保持报告里的出现顺序，后续再按分数稳定排序。
        LinkedHashMap<String, WeakDomain> unique = new LinkedHashMap<>();
        for (WeakDomain weakDomain : weakDomains) {
            if (weakDomain == null || weakDomain.domain() == null || weakDomain.domain().isBlank()) {
                throw new InvalidPlanInputException("weak_domains 含空 domain");
            }
            double score = weakDomain.score();
            if (!Double.isFinite(score) || score < 0 || score > 5) {
                throw new InvalidPlanInputException("weak_domains.score 必须在 0–5");
            }
            unique.putIfAbsent(weakDomain.domain(), weakDomain);
        }
        List<WeakDomain> ordered = new ArrayList<>(unique.values());
        if (ordered.size() > MAX_TASKS) {
            throw new InvalidPlanInputException("weak_domains 最多 " + MAX_TASKS + " 个");
        }
        ordered.sort(Comparator.comparingDouble(WeakDomain::score));

        LocalDate today = LocalDate.now(clock.withZone(zone));
        List<TaskDraft> tasks = new ArrayList<>(ordered.size());
        for (int index = 0; index < ordered.size(); index++) {
            WeakDomain weakDomain = ordered.get(index);
            String title = weakDomain.advice() == null || weakDomain.advice().isBlank()
                    ? DEFAULT_TASK_TITLE
                    : weakDomain.advice().trim();
            Instant dueAt = today.plusDays(index)
                    .atTime(23, 59, 59)
                    .atZone(zone)
                    .toInstant();
            tasks.add(new TaskDraft(weakDomain.domain(), title, dueAt, index + 1));
        }
        return new PlanDecision.Create(new PlanDraft(goal(input), List.copyOf(tasks)));
    }

    private static String goal(PlanInput input) {
        if (input.goal() != null && !input.goal().isBlank()) {
            return input.goal().trim();
        }
        if (input.position() != null && !input.position().isBlank()) {
            return "针对「" + input.position().trim() + "」的面试短板学习计划";
        }
        return "面试短板补强计划";
    }
}
