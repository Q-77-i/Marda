package com.marda.business.outbox;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.marda.business.event.EventEnvelope;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * business 侧 outbox 仓储。
 *
 * <p>领取用 {@code FOR UPDATE SKIP LOCKED} + 租约，多 relay 安全；发布成功才写
 * {@code published_at}，失败只记 attempts / 退避时间。语义是**至少一次**。
 */
@Repository
public class BusinessOutboxRepository {

    private final JdbcClient jdbc;
    private final ObjectMapper objectMapper;

    public BusinessOutboxRepository(JdbcClient jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    /** 同一聚合 + 事件类型只入队一次；重复调用返回 false（不抛 409）。 */
    public boolean insert(
            EventEnvelope event,
            String aggregateType,
            String aggregateId,
            String partitionKey,
            Instant now) {
        String payload;
        try {
            payload = objectMapper.writeValueAsString(event);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("事件序列化失败", exception);
        }
        int inserted = jdbc.sql("""
                INSERT INTO business_outbox
                    (id, aggregate_type, aggregate_id, event_type, partition_key,
                     payload, created_at, next_attempt_at)
                VALUES
                    (:id, :aggregateType, :aggregateId, :eventType, :partitionKey,
                     CAST(:payload AS jsonb), :createdAt, :nextAttemptAt)
                ON CONFLICT DO NOTHING
                """)
                .param("id", UUID.fromString(event.eventId()))
                .param("aggregateType", aggregateType)
                .param("aggregateId", aggregateId)
                .param("eventType", event.type())
                .param("partitionKey", partitionKey)
                .param("payload", payload)
                .param("createdAt", Timestamp.from(now))
                .param("nextAttemptAt", Timestamp.from(now))
                .update();
        return inserted == 1;
    }

    /** 领一批：租约写入 next_attempt_at；崩溃后租约到期由其他 relay 接手。 */
    public List<OutboxRow> claim(int limit, int leaseSeconds, Instant now) {
        Instant leaseUntil = now.plusSeconds(leaseSeconds);
        return jdbc.sql("""
                WITH batch AS (
                    SELECT id FROM business_outbox
                    WHERE published_at IS NULL
                      AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
                    ORDER BY created_at, id
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE business_outbox o
                SET next_attempt_at = :leaseUntil
                FROM batch
                WHERE o.id = batch.id
                RETURNING o.id, o.event_type, o.partition_key, o.payload::text AS payload,
                          o.attempts, o.created_at, o.next_attempt_at
                """)
                .param("now", Timestamp.from(now))
                .param("limit", limit)
                .param("leaseUntil", Timestamp.from(leaseUntil))
                .query((rs, rowNum) -> new OutboxRow(
                        rs.getObject("id", UUID.class),
                        rs.getString("event_type"),
                        rs.getString("partition_key"),
                        rs.getString("payload"),
                        rs.getInt("attempts"),
                        rs.getObject("created_at", OffsetDateTime.class).toInstant(),
                        rs.getObject("next_attempt_at", OffsetDateTime.class) == null
                                ? null
                                : rs.getObject("next_attempt_at", OffsetDateTime.class).toInstant()))
                .list();
    }

    public int markPublished(List<UUID> ids, Instant now) {
        if (ids.isEmpty()) {
            return 0;
        }
        return jdbc.sql("""
                UPDATE business_outbox
                SET published_at = :now, last_error = NULL
                WHERE id IN (:ids)
                """)
                .param("now", Timestamp.from(now))
                .param("ids", ids)
                .update();
    }

    public int markFailed(UUID id, String error, Instant nextAttemptAt) {
        String message = error == null ? "publish failed" : error;
        return jdbc.sql("""
                UPDATE business_outbox
                SET attempts = attempts + 1,
                    last_error = :error,
                    next_attempt_at = :nextAttemptAt
                WHERE id = :id
                """)
                .param("error", message.length() > 500 ? message.substring(0, 500) : message)
                .param("nextAttemptAt", Timestamp.from(nextAttemptAt))
                .param("id", id)
                .update();
    }

    public int countPending() {
        return jdbc.sql("SELECT count(*) FROM business_outbox WHERE published_at IS NULL")
                .query(Integer.class)
                .single();
    }

    /** 已发布且超过保留期的行才删；未发布的是待补发的事实，永远不删。 */
    public int cleanup(int retentionDays, Instant now) {
        return jdbc.sql("""
                DELETE FROM business_outbox
                WHERE published_at IS NOT NULL
                  AND published_at < :cutoff
                """)
                .param("cutoff", Timestamp.from(now.minusSeconds(retentionDays * 86400L)))
                .update();
    }
}
