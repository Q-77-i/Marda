package com.marda.business.plan.persistence;

import com.marda.business.plan.domain.LearningPlanSkip;
import com.marda.business.plan.domain.SkipReason;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;

/** 跳过审计仓储：同一 source_interview_id 只留一条。 */
@Repository
public class LearningPlanSkipRepository {

    private static final RowMapper<LearningPlanSkip> SKIP_ROW = (rs, rowNum) -> new LearningPlanSkip(
            UUID.fromString(rs.getString("id")),
            rs.getString("user_id"),
            rs.getString("source_interview_id"),
            SkipReason.valueOf(rs.getString("reason")),
            rs.getObject("created_at", OffsetDateTime.class).toInstant());

    private final JdbcClient jdbc;

    public LearningPlanSkipRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<LearningPlanSkip> findBySourceInterviewId(String sourceInterviewId) {
        return jdbc.sql("""
                SELECT id, user_id, source_interview_id, reason, created_at
                FROM learning_plan_skip
                WHERE source_interview_id = :sourceInterviewId
                """)
                .param("sourceInterviewId", sourceInterviewId)
                .query(SKIP_ROW)
                .optional();
    }

    public int insertIfAbsent(LearningPlanSkip skip) {
        return jdbc.sql("""
                INSERT INTO learning_plan_skip
                    (id, user_id, source_interview_id, reason, created_at)
                VALUES
                    (:id, :userId, :sourceInterviewId, :reason, :createdAt)
                ON CONFLICT (source_interview_id) DO NOTHING
                """)
                .param("id", skip.id())
                .param("userId", skip.userId())
                .param("sourceInterviewId", skip.sourceInterviewId())
                .param("reason", skip.reason().name())
                .param("createdAt", Timestamp.from(skip.createdAt()))
                .update();
    }
}
