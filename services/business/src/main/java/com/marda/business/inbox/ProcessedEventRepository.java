package com.marda.business.inbox;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.UUID;

/**
 * 消费 inbox：`(event_id, consumer)` 唯一。
 *
 * <p>与业务写入**同一事务**调用：重复事件直接 ack；业务失败则连同去重记录一起回滚，
 * 消息重投时仍会重新处理。
 */
@Repository
public class ProcessedEventRepository {

    private final JdbcClient jdbc;

    public ProcessedEventRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** true = 首次处理；false = 已处理过（重复消息，调用方直接返回）。 */
    public boolean markProcessed(UUID eventId, String consumer, Instant now) {
        return jdbc.sql("""
                INSERT INTO processed_event (event_id, consumer, processed_at)
                VALUES (:eventId, :consumer, :processedAt)
                ON CONFLICT DO NOTHING
                """)
                .param("eventId", eventId)
                .param("consumer", consumer)
                .param("processedAt", Timestamp.from(now))
                .update() == 1;
    }
}
