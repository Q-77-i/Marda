package com.marda.business.plan.persistence;

import com.marda.business.plan.domain.LearningPlan;
import com.marda.business.plan.domain.PlanStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/** 计划头仓储：幂等创建、按用户读、原子完成。 */
@Repository
public class LearningPlanRepository {

    private static final RowMapper<LearningPlan> PLAN_ROW = (rs, rowNum) -> new LearningPlan(
            UUID.fromString(rs.getString("id")),
            rs.getString("user_id"),
            rs.getString("source_interview_id"),
            rs.getString("goal"),
            PlanStatus.fromApi(rs.getString("status")),
            rs.getLong("version"),
            rs.getString("idempotency_key"),
            rs.getString("request_hash"),
            rs.getObject("created_at", OffsetDateTime.class).toInstant(),
            rs.getObject("updated_at", OffsetDateTime.class).toInstant());

    private final JdbcClient jdbc;

    public LearningPlanRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 返回 1 = 本次插入；0 = source_interview_id 已存在（并发安全的先查后插替代）。 */
    public int insertPlanIfAbsent(LearningPlan plan) {
        return jdbc.sql("""
                INSERT INTO learning_plan
                    (id, user_id, source_interview_id, goal, status, version,
                     idempotency_key, request_hash, created_at, updated_at)
                VALUES
                    (:id, :userId, :sourceInterviewId, :goal, :status, :version,
                     :idempotencyKey, :requestHash, :createdAt, :updatedAt)
                ON CONFLICT (source_interview_id) DO NOTHING
                """)
                .param("id", plan.id())
                .param("userId", plan.userId())
                .param("sourceInterviewId", plan.sourceInterviewId())
                .param("goal", plan.goal())
                .param("status", plan.status().apiValue())
                .param("version", plan.version())
                .param("idempotencyKey", plan.idempotencyKey())
                .param("requestHash", plan.requestHash())
                .param("createdAt", Timestamp.from(plan.createdAt()))
                .param("updatedAt", Timestamp.from(plan.updatedAt()))
                .update();
    }

    public Optional<LearningPlan> findBySourceInterviewId(String sourceInterviewId) {
        return jdbc.sql("""
                SELECT id, user_id, source_interview_id, goal, status, version,
                       idempotency_key, request_hash, created_at, updated_at
                FROM learning_plan
                WHERE source_interview_id = :sourceInterviewId
                """)
                .param("sourceInterviewId", sourceInterviewId)
                .query(PLAN_ROW)
                .optional();
    }

    public Optional<LearningPlan> findByIdAndUserId(UUID id, String userId) {
        return jdbc.sql("""
                SELECT id, user_id, source_interview_id, goal, status, version,
                       idempotency_key, request_hash, created_at, updated_at
                FROM learning_plan
                WHERE id = :id AND user_id = :userId
                """)
                .param("id", id)
                .param("userId", userId)
                .query(PLAN_ROW)
                .optional();
    }

    public List<LearningPlan> listByUserId(String userId, PlanStatus status, int limit, int offset) {
        StringBuilder sql = new StringBuilder("""
                SELECT id, user_id, source_interview_id, goal, status, version,
                       idempotency_key, request_hash, created_at, updated_at
                FROM learning_plan
                WHERE user_id = :userId
                """);
        if (status != null) {
            sql.append(" AND status = :status");
        }
        sql.append(" ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset");

        JdbcClient.StatementSpec spec = jdbc.sql(sql.toString()).param("userId", userId);
        if (status != null) {
            spec = spec.param("status", status.apiValue());
        }
        return spec.param("limit", limit).param("offset", offset).query(PLAN_ROW).list();
    }

    public long countByUserId(String userId, PlanStatus status) {
        StringBuilder sql = new StringBuilder(
                "SELECT count(*) FROM learning_plan WHERE user_id = :userId");
        if (status != null) {
            sql.append(" AND status = :status");
        }
        JdbcClient.StatementSpec spec = jdbc.sql(sql.toString()).param("userId", userId);
        if (status != null) {
            spec = spec.param("status", status.apiValue());
        }
        return spec.query(Long.class).single();
    }

    /** 原子完成：只有计划仍 active 且不再有非终态任务时才更新。 */
    public int completePlanIfAllTasksTerminal(UUID planId, Instant now) {
        return jdbc.sql("""
                UPDATE learning_plan p
                SET status = 'completed', version = version + 1, updated_at = :now
                WHERE p.id = :planId
                  AND p.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_task t
                      WHERE t.plan_id = p.id AND t.status IN ('todo', 'in_progress')
                  )
                """)
                .param("planId", planId)
                .param("now", Timestamp.from(now))
                .update();
    }
}
