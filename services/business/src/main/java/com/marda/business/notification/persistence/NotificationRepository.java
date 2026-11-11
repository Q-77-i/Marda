package com.marda.business.notification.persistence;

import com.marda.business.notification.domain.Notification;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.sql.Types;

/** 通知写入仓储；J3 只插入，不查询/投递。 */
@Repository
public class NotificationRepository {

    private final JdbcClient jdbc;

    public NotificationRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void insert(Notification notification) {
        jdbc.sql("""
                INSERT INTO notification
                    (id, user_id, channel, template, payload, status, retry_count,
                     next_retry_at, idempotency_key, created_at)
                VALUES
                    (:id, :userId, :channel, :template, CAST(:payload AS jsonb), :status,
                     :retryCount, CAST(:nextRetryAt AS timestamptz),
                     CAST(:idempotencyKey AS text), :createdAt)
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
                .update();
    }
}
