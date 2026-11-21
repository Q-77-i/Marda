package com.marda.business.notification.persistence;

import com.marda.business.notification.domain.Notification;
import com.marda.business.notification.domain.NotificationChannel;
import com.marda.business.notification.domain.NotificationStatus;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.sql.Types;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/** 通知仓储：写入（计划 / 测试提醒）+ worker 的状态迁移 + 用户读接口。 */
@Repository
public class NotificationRepository {

    private static final RowMapper<Notification> ROW_MAPPER = (rs, rowNum) -> new Notification(
            rs.getObject("id", UUID.class),
            rs.getString("user_id"),
            NotificationChannel.fromApi(rs.getString("channel")),
            rs.getString("template"),
            rs.getString("payload"),
            NotificationStatus.fromApi(rs.getString("status")),
            rs.getInt("retry_count"),
            toInstant(rs.getObject("next_retry_at", OffsetDateTime.class)),
            rs.getString("idempotency_key"),
            toInstant(rs.getObject("created_at", OffsetDateTime.class)),
            toInstant(rs.getObject("sent_at", OffsetDateTime.class)));

    private final JdbcClient jdbc;

    public NotificationRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    private static Instant toInstant(OffsetDateTime value) {
        return value == null ? null : value.toInstant();
    }

    public void insert(Notification notification) {
        jdbc.sql("""
                INSERT INTO notification
                    (id, user_id, channel, template, payload, status, retry_count,
                     next_retry_at, idempotency_key, created_at, sent_at)
                VALUES
                    (:id, :userId, :channel, :template, CAST(:payload AS jsonb), :status,
                     :retryCount, CAST(:nextRetryAt AS timestamptz),
                     CAST(:idempotencyKey AS text), :createdAt, CAST(:sentAt AS timestamptz))
                """)
                .param("id", notification.id())
                .param("userId", notification.userId())
                .param("channel", notification.channel().apiValue())
                .param("template", notification.template())
                .param("payload", notification.payloadJson())
                .param("status", notification.status().apiValue())
                .param("retryCount", notification.retryCount())
                .param("nextRetryAt",
                        notification.nextRetryAt() == null ? null : Timestamp.from(notification.nextRetryAt()),
                        Types.TIMESTAMP)
                .param("idempotencyKey", notification.idempotencyKey(), Types.VARCHAR)
                .param("createdAt", Timestamp.from(notification.createdAt()))
                .param("sentAt",
                        notification.sentAt() == null ? null : Timestamp.from(notification.sentAt()),
                        Types.TIMESTAMP)
                .update();
    }

    public Optional<Notification> findById(UUID id) {
        return jdbc.sql("SELECT * FROM notification WHERE id = :id")
                .param("id", id)
                .query(ROW_MAPPER)
                .optional();
    }

    public List<Notification> listByUserId(String userId, NotificationStatus status, int limit) {
        return jdbc.sql("""
                SELECT * FROM notification
                WHERE user_id = :userId AND status = :status
                ORDER BY created_at DESC
                LIMIT :limit
                """)
                .param("userId", userId)
                .param("status", status.apiValue())
                .param("limit", limit)
                .query(ROW_MAPPER)
                .list();
    }

    public int markSent(UUID id, Instant now) {
        return jdbc.sql("""
                UPDATE notification
                SET status = 'sent', sent_at = :now, next_retry_at = NULL
                WHERE id = :id
                """)
                .param("now", Timestamp.from(now))
                .param("id", id)
                .update();
    }

    public int markFailed(UUID id, Instant nextRetryAt) {
        return jdbc.sql("""
                UPDATE notification
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    next_retry_at = :nextRetryAt
                WHERE id = :id AND status IN ('pending', 'failed')
                """)
                .param("nextRetryAt", Timestamp.from(nextRetryAt))
                .param("id", id)
                .update();
    }

    public int markDead(UUID id) {
        return jdbc.sql("UPDATE notification SET status = 'dead' WHERE id = :id")
                .param("id", id)
                .update();
    }
}
