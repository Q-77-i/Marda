package com.marda.business.plan.persistence;

import com.marda.business.plan.domain.LearningTask;
import com.marda.business.plan.domain.TaskStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.sql.Types;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/** 任务仓储：任务独立于计划头更新，查询按 plan_id / user_id 走索引。 */
@Repository
public class LearningTaskRepository {

    private static final RowMapper<LearningTask> TASK_ROW = (rs, rowNum) -> new LearningTask(
            UUID.fromString(rs.getString("id")),
            UUID.fromString(rs.getString("plan_id")),
            rs.getString("domain"),
            rs.getString("title"),
            rs.getString("resource_url"),
            TaskStatus.fromApi(rs.getString("status")),
            rs.getObject("due_at", OffsetDateTime.class) == null
                    ? null
                    : rs.getObject("due_at", OffsetDateTime.class).toInstant(),
            rs.getInt("priority"),
            rs.getLong("version"),
            rs.getObject("created_at", OffsetDateTime.class).toInstant(),
            rs.getObject("updated_at", OffsetDateTime.class).toInstant());

    private final JdbcClient jdbc;

    public LearningTaskRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void insertAll(List<LearningTask> tasks) {
        for (LearningTask task : tasks) {
            jdbc.sql("""
                    INSERT INTO learning_task
                        (id, plan_id, domain, title, resource_url, status, due_at,
                         priority, version, created_at, updated_at)
                    VALUES
                        (:id, :planId, :domain, :title, :resourceUrl, :status,
                         CAST(:dueAt AS timestamptz), :priority, :version, :createdAt, :updatedAt)
                    """)
                    .param("id", task.id())
                    .param("planId", task.planId())
                    .param("domain", task.domain())
                    .param("title", task.title())
                    .param("resourceUrl", task.resourceUrl(), Types.VARCHAR)
                    .param("status", task.status().apiValue())
                    .param("dueAt", task.dueAt() == null ? null : Timestamp.from(task.dueAt()), Types.TIMESTAMP)
                    .param("priority", task.priority())
                    .param("version", task.version())
                    .param("createdAt", Timestamp.from(task.createdAt()))
                    .param("updatedAt", Timestamp.from(task.updatedAt()))
                    .update();
        }
    }

    public List<LearningTask> findByPlanId(UUID planId) {
        return jdbc.sql("""
                SELECT id, plan_id, domain, title, resource_url, status, due_at,
                       priority, version, created_at, updated_at
                FROM learning_task
                WHERE plan_id = :planId
                ORDER BY priority ASC, due_at ASC NULLS LAST, created_at ASC
                """)
                .param("planId", planId)
                .query(TASK_ROW)
                .list();
    }

    public List<LearningTask> findByPlanIds(List<UUID> planIds) {
        if (planIds.isEmpty()) {
            return List.of();
        }
        String placeholders = String.join(",", Collections.nCopies(planIds.size(), "?"));
        JdbcClient.StatementSpec spec = jdbc.sql("""
                SELECT id, plan_id, domain, title, resource_url, status, due_at,
                       priority, version, created_at, updated_at
                FROM learning_task
                WHERE plan_id IN (%s)
                ORDER BY priority ASC, due_at ASC NULLS LAST, created_at ASC
                """.formatted(placeholders));
        for (UUID planId : planIds) {
            spec = spec.param(planId);
        }
        return spec.query(TASK_ROW).list();
    }

    public Optional<LearningTask> findById(UUID id) {
        return jdbc.sql("""
                SELECT id, plan_id, domain, title, resource_url, status, due_at,
                       priority, version, created_at, updated_at
                FROM learning_task
                WHERE id = :id
                """)
                .param("id", id)
                .query(TASK_ROW)
                .optional();
    }

    /** 任务 + 计划归属一起判：越权统一 404，不泄露存在性。 */
    public Optional<LearningTask> findByIdAndUserId(UUID taskId, String userId) {
        return jdbc.sql("""
                SELECT t.id, t.plan_id, t.domain, t.title, t.resource_url, t.status,
                       t.due_at, t.priority, t.version, t.created_at, t.updated_at
                FROM learning_task t
                JOIN learning_plan p ON p.id = t.plan_id
                WHERE t.id = :taskId AND p.user_id = :userId
                """)
                .param("taskId", taskId)
                .param("userId", userId)
                .query(TASK_ROW)
                .optional();
    }

    /** 条件更新：返回 0 表示版本已被别人推进（或任务已不存在）。 */
    public int updateTask(UUID taskId, long expectedVersion, TaskStatus status, Instant dueAt, Instant now) {
        return jdbc.sql("""
                UPDATE learning_task
                SET status = :status,
                    due_at = CAST(:dueAt AS timestamptz),
                    version = version + 1,
                    updated_at = :now
                WHERE id = :id AND version = :expectedVersion
                """)
                .param("status", status.apiValue())
                .param("dueAt", dueAt == null ? null : Timestamp.from(dueAt), Types.TIMESTAMP)
                .param("now", Timestamp.from(now))
                .param("id", taskId)
                .param("expectedVersion", expectedVersion)
                .update();
    }

    public List<LearningTask> findTodayTasks(String userId, Instant endOfToday) {
        return jdbc.sql("""
                SELECT t.id, t.plan_id, t.domain, t.title, t.resource_url, t.status,
                       t.due_at, t.priority, t.version, t.created_at, t.updated_at
                FROM learning_task t
                JOIN learning_plan p ON p.id = t.plan_id
                WHERE p.user_id = :userId
                  AND p.status = 'active'
                  AND t.status IN ('todo', 'in_progress')
                  AND (t.due_at IS NULL OR t.due_at < :endOfToday)
                ORDER BY t.priority ASC, t.due_at ASC NULLS LAST, t.created_at ASC
                """)
                .param("userId", userId)
                .param("endOfToday", Timestamp.from(endOfToday))
                .query(TASK_ROW)
                .list();
    }

    public List<LearningTask> findNextTasks(String userId, int limit) {
        return jdbc.sql("""
                SELECT t.id, t.plan_id, t.domain, t.title, t.resource_url, t.status,
                       t.due_at, t.priority, t.version, t.created_at, t.updated_at
                FROM learning_task t
                JOIN learning_plan p ON p.id = t.plan_id
                WHERE p.user_id = :userId
                  AND p.status = 'active'
                  AND t.status IN ('todo', 'in_progress')
                ORDER BY t.priority ASC, t.due_at ASC NULLS LAST, t.created_at ASC
                LIMIT :limit
                """)
                .param("userId", userId)
                .param("limit", limit)
                .query(TASK_ROW)
                .list();
    }
}
