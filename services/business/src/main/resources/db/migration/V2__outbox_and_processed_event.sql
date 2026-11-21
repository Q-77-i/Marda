-- P5-J5：outbox / 消费去重 / 通知送达时间。
--
-- 一张表一个写者：
-- - business_outbox 只由 business / worker 写（同一服务，同一本地事务）；
-- - processed_event 是消费者的 inbox，与业务写入同一事务（重复事件直接 ack）；
-- - notification.sent_at 供读接口 / 后续送达指标；pending 行不填。
--
-- 不设长期保留任务的触发器：outbox 清理由 relay 每天做（published 且超过保留期）。

ALTER TABLE notification ADD COLUMN sent_at TIMESTAMPTZ;

CREATE TABLE business_outbox (
    id              UUID PRIMARY KEY,
    aggregate_type  TEXT        NOT NULL,
    aggregate_id    TEXT        NOT NULL,
    event_type      TEXT        NOT NULL,
    partition_key   TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    published_at    TIMESTAMPTZ,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    last_error      TEXT,
    CONSTRAINT uq_business_outbox_event UNIQUE (aggregate_type, aggregate_id, event_type),
    CONSTRAINT ck_business_outbox_attempts CHECK (attempts >= 0)
);

-- relay 只扫未发布行：部分索引让积压查询与领取都走这条。
CREATE INDEX idx_business_outbox_pending
    ON business_outbox (next_attempt_at, created_at)
    WHERE published_at IS NULL;

CREATE TABLE processed_event (
    event_id     UUID        NOT NULL,
    consumer     TEXT        NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (event_id, consumer)
);

CREATE INDEX idx_processed_event_processed_at ON processed_event (processed_at);
