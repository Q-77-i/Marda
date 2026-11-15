package com.marda.business.plan.application;

import com.marda.business.cache.BusinessCache;
import com.marda.business.cache.BusinessCacheProperties;
import com.marda.business.cache.CacheKeys;
import com.marda.business.cache.CacheResult;
import com.marda.business.common.BadRequestException;
import com.marda.business.plan.domain.IdempotencyHasher;
import com.marda.business.plan.domain.InvalidPlanInputException;
import com.marda.business.plan.domain.LearningPlan;
import com.marda.business.plan.domain.LearningPlanSkip;
import com.marda.business.plan.domain.LearningTask;
import com.marda.business.plan.domain.PlanDecision;
import com.marda.business.plan.domain.PlanDraft;
import com.marda.business.plan.domain.PlanFactory;
import com.marda.business.plan.domain.PlanInput;
import com.marda.business.plan.domain.PlanStatus;
import com.marda.business.plan.domain.TaskStateMachine;
import com.marda.business.plan.domain.TaskStatus;
import com.marda.business.plan.persistence.LearningPlanRepository;
import com.marda.business.plan.persistence.LearningPlanSkipRepository;
import com.marda.business.plan.persistence.LearningTaskRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

/** 学习计划应用服务：事务边界在这里，SQL 在仓储里。 */
@Service
public class LearningPlanService {

    private final LearningPlanRepository plans;
    private final LearningTaskRepository tasks;
    private final LearningPlanSkipRepository skips;
    private final PlanFactory factory;
    private final IdempotencyHasher hasher;
    private final Clock clock;
    private final ZoneId zone;
    private final BusinessCache cache;
    private final BusinessCacheProperties cacheProperties;

    public LearningPlanService(
            LearningPlanRepository plans,
            LearningTaskRepository tasks,
            LearningPlanSkipRepository skips,
            PlanFactory factory,
            IdempotencyHasher hasher,
            Clock clock,
            ZoneId zone,
            BusinessCache cache,
            BusinessCacheProperties cacheProperties) {
        this.plans = plans;
        this.tasks = tasks;
        this.skips = skips;
        this.factory = factory;
        this.hasher = hasher;
        this.clock = clock;
        this.zone = zone;
        this.cache = cache;
        this.cacheProperties = cacheProperties;
    }

    /**
     * 建计划：计划 + 任务在同一本地事务；并发靠 `ON CONFLICT` 唯一键。
     *
     * <p>公共路径同 key 不同体 → 409；内部路径已存在 → 200（事件重放不制造重试/DLQ）。
     */
    @Transactional
    public CreatePlanResult create(PlanInput input, String idempotencyKey, boolean internal) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new BadRequestException("Idempotency-Key 不能为空");
        }
        if (!internal && !idempotencyKey.equals(input.sourceInterviewId())) {
            throw new BadRequestException("Idempotency-Key 必须等于 source_interview_id");
        }

        Optional<LearningPlanSkip> existingSkip =
                skips.findBySourceInterviewId(input.sourceInterviewId());
        if (existingSkip.isPresent()) {
            return CreatePlanResult.skipped(existingSkip.get().reason());
        }

        PlanDecision decision = factory.decide(input);
        Instant now = clock.instant();
        if (decision instanceof PlanDecision.Skip skip) {
            skips.insertIfAbsent(new LearningPlanSkip(
                    UUID.randomUUID(), input.userId(), input.sourceInterviewId(), skip.reason(), now));
            return CreatePlanResult.skipped(skip.reason());
        }

        PlanDraft draft = ((PlanDecision.Create) decision).draft();
        String requestHash = hasher.hash(input);
        LearningPlan plan = new LearningPlan(
                UUID.randomUUID(),
                input.userId(),
                input.sourceInterviewId(),
                draft.goal(),
                PlanStatus.ACTIVE,
                1L,
                idempotencyKey,
                requestHash,
                now,
                now);

        int inserted = plans.insertPlanIfAbsent(plan);
        if (inserted == 0) {
            LearningPlan existing = plans.findBySourceInterviewId(input.sourceInterviewId())
                    .orElseThrow(() -> new IllegalStateException("计划插入冲突但读不到现有行"));
            if (internal || existing.requestHash().equals(requestHash)) {
                return CreatePlanResult.existing(new PlanView(
                        existing, tasks.findByPlanId(existing.id())));
            }
            throw new PlanConflictException(
                    "IDEMPOTENCY_CONFLICT",
                    "同一 Idempotency-Key 的请求体不同",
                    Map.of("plan_id", existing.id().toString()));
        }

        List<LearningTask> taskRows = draft.tasks().stream()
                .map(taskDraft -> new LearningTask(
                        UUID.randomUUID(),
                        plan.id(),
                        taskDraft.domain(),
                        taskDraft.title(),
                        null,
                        TaskStatus.TODO,
                        taskDraft.dueAt(),
                        taskDraft.priority(),
                        1L,
                        now,
                        now))
                .toList();
        tasks.insertAll(taskRows);
        evictAfterCommit(
                CacheKeys.today(input.userId(), todayDate()),
                CacheKeys.plan(input.userId(), plan.id()));
        return CreatePlanResult.created(new PlanView(plan, taskRows));
    }

    @Transactional(readOnly = true)
    public PageResult<PlanView> list(String userId, PlanStatus status, int page, int pageSize) {
        int offset = (page - 1) * pageSize;
        List<LearningPlan> rows = plans.listByUserId(userId, status, pageSize, offset);
        long total = plans.countByUserId(userId, status);
        Map<UUID, List<LearningTask>> byPlan = tasks
                .findByPlanIds(rows.stream().map(LearningPlan::id).toList())
                .stream()
                .collect(Collectors.groupingBy(LearningTask::planId));
        List<PlanView> views = rows.stream()
                .map(plan -> new PlanView(plan, byPlan.getOrDefault(plan.id(), List.of())))
                .toList();
        return new PageResult<>(views, total, page, pageSize);
    }

    @Transactional(readOnly = true)
    public PlanView get(String userId, UUID planId) {
        return getCached(userId, planId).value();
    }

    /** 详情读缓存：键含 user（防越权），写任务后失效。 */
    @Transactional(readOnly = true)
    public CacheResult<PlanView> getCached(String userId, UUID planId) {
        return cache.getOrLoad(
                "learning.plan",
                CacheKeys.plan(userId, planId),
                PlanView.class,
                () -> loadPlan(userId, planId),
                cacheProperties.planTtl(),
                cacheProperties.nullTtl(),
                false);
    }

    private PlanView loadPlan(String userId, UUID planId) {
        LearningPlan plan = plans.findByIdAndUserId(planId, userId)
                .orElseThrow(() -> new PlanNotFoundException("学习计划不存在"));
        return new PlanView(plan, tasks.findByPlanId(planId));
    }

    @Transactional
    public LearningTask updateTask(String userId, UUID taskId, TaskUpdateCommand command) {
        LearningTask task = tasks.findByIdAndUserId(taskId, userId)
                .orElseThrow(() -> new PlanNotFoundException("任务不存在"));

        boolean statusPresent = command.status() != null;
        boolean duePresent = command.dueAtPresent();
        if (!statusPresent && !duePresent) {
            throw new InvalidPlanInputException("至少提供 status 或 due_at");
        }
        if (command.version() != task.version()) {
            throw versionConflict(task);
        }

        TaskStatus targetStatus = statusPresent ? parseStatus(command.status()) : task.status();
        if (statusPresent && !TaskStateMachine.canTransition(task.status(), targetStatus)) {
            throw stateConflict(task, "任务状态不允许该转换");
        }
        if (task.status().terminal() && duePresent) {
            throw stateConflict(task, "终态任务的截止时间不可修改");
        }

        Instant targetDueAt = duePresent ? command.dueAt() : task.dueAt();
        if (targetStatus == task.status() && Objects.equals(targetDueAt, task.dueAt())) {
            return task;
        }

        Instant now = clock.instant();
        int updated = tasks.updateTask(task.id(), command.version(), targetStatus, targetDueAt, now);
        if (updated == 0) {
            LearningTask current = tasks.findById(task.id())
                    .orElseThrow(() -> new PlanNotFoundException("任务不存在"));
            throw versionConflict(current);
        }

        LearningTask saved = tasks.findById(task.id())
                .orElseThrow(() -> new IllegalStateException("任务更新后读不到"));
        if (targetStatus.terminal()) {
            plans.completePlanIfAllTasksTerminal(task.planId(), now);
        }
        evictAfterCommit(
                CacheKeys.today(userId, todayDate()),
                CacheKeys.plan(userId, task.planId()));
        return saved;
    }

    @Transactional(readOnly = true)
    public TodayView today(String userId) {
        return todayCached(userId).value();
    }

    /** 今日任务读缓存：日期进 key（跨午夜不会读到昨天的列表），写任务后失效。 */
    @Transactional(readOnly = true)
    public CacheResult<TodayView> todayCached(String userId) {
        LocalDate date = todayDate();
        Instant endOfToday = date.plusDays(1).atStartOfDay(zone).toInstant();
        return cache.getOrLoad(
                "learning.today",
                CacheKeys.today(userId, date),
                TodayView.class,
                () -> new TodayView(date, tasks.findTodayTasks(userId, endOfToday)),
                cacheProperties.todayTtl(),
                cacheProperties.nullTtl(),
                false);
    }

    private LocalDate todayDate() {
        return LocalDate.now(clock.withZone(zone));
    }

    /**
     * 写事务提交后再删缓存：提交前删会被并发读立刻用旧值回填。
     * 没有事务上下文（单测 / 直调）时立即删。
     */
    private void evictAfterCommit(String... keys) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            for (String key : keys) {
                cache.evict(key);
            }
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                for (String key : keys) {
                    cache.evict(key);
                }
            }
        });
    }

    @Transactional(readOnly = true)
    public NextView next(String userId, int limit) {
        List<LearningTask> rows = tasks.findNextTasks(userId, limit);
        if (rows.isEmpty()) {
            return new NextView(null, List.of(), null);
        }
        return new NextView(rows.get(0).planId(), rows, clock.instant());
    }

    private static TaskStatus parseStatus(String value) {
        try {
            return TaskStatus.fromApi(value);
        } catch (IllegalArgumentException exception) {
            throw new InvalidPlanInputException("未知任务状态：" + value);
        }
    }

    private static PlanConflictException stateConflict(LearningTask task, String message) {
        return new PlanConflictException(
                "STATE_CONFLICT", message, Map.of("current_status", task.status().apiValue()));
    }

    private static PlanConflictException versionConflict(LearningTask task) {
        return new PlanConflictException(
                "VERSION_CONFLICT",
                "任务已被更新，请刷新后重试",
                Map.of(
                        "current_version", task.version(),
                        "current_status", task.status().apiValue()));
    }
}
