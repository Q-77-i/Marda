-- P5-J3：Java 业务库（marda_business）初始 schema。
--
-- 一张表一个写者：本库只由 business 服务写；Python 域（interviews/reports/...）不在本库。
-- J3 不建 business_outbox / processed_event / user_preference —— 它们随 J5 的 outbox 与
-- 通知 worker 一起引入，避免死表。

CREATE TABLE learning_plan (
    id                  UUID PRIMARY KEY,
    user_id             TEXT        NOT NULL,
    source_interview_id TEXT        NOT NULL,
    goal                TEXT        NOT NULL,
    status              TEXT        NOT NULL,
    version             BIGINT      NOT NULL DEFAULT 1,
    idempotency_key     TEXT        NOT NULL,
    request_hash        TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_learning_plan_source_interview UNIQUE (source_interview_id),
    CONSTRAINT ck_learning_plan_status CHECK (status IN ('active', 'completed', 'archived')),
    CONSTRAINT ck_learning_plan_version CHECK (version >= 1)
);

CREATE INDEX idx_learning_plan_user_status ON learning_plan (user_id, status);

CREATE TABLE learning_task (
    id           UUID PRIMARY KEY,
    plan_id      UUID        NOT NULL REFERENCES learning_plan (id) ON DELETE CASCADE,
    domain       TEXT        NOT NULL,
    title        TEXT        NOT NULL,
    resource_url TEXT,
    status       TEXT        NOT NULL,
    due_at       TIMESTAMPTZ,
    priority     INTEGER     NOT NULL,
    version      BIGINT      NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_learning_task_status CHECK (status IN ('todo', 'in_progress', 'done', 'skipped')),
    CONSTRAINT ck_learning_task_priority CHECK (priority BETWEEN 1 AND 5),
    CONSTRAINT ck_learning_task_version CHECK (version >= 1),
    -- 计划工厂已去重；数据库再守一道，防止未来批量导入 / 手工 SQL 造出同域重复任务。
    CONSTRAINT uq_learning_task_plan_domain UNIQUE (plan_id, domain)
);

CREATE INDEX idx_learning_task_plan_status ON learning_task (plan_id, status);
CREATE INDEX idx_learning_task_priority_due ON learning_task (priority, due_at);

CREATE TABLE notification (
    id              UUID PRIMARY KEY,
    user_id         TEXT        NOT NULL,
    channel         TEXT        NOT NULL,
    template        TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    status          TEXT        NOT NULL,
    retry_count     INTEGER     NOT NULL DEFAULT 0,
    next_retry_at   TIMESTAMPTZ,
    idempotency_key TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_notification_channel CHECK (channel IN ('in_app')),
    CONSTRAINT ck_notification_status CHECK (status IN ('pending', 'sent', 'failed', 'dead')),
    CONSTRAINT ck_notification_retry_count CHECK (retry_count >= 0),
    CONSTRAINT uq_notification_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX idx_notification_user_created ON notification (user_id, created_at DESC);
CREATE INDEX idx_notification_status_retry ON notification (status, next_retry_at);

CREATE TABLE learning_plan_skip (
    id                  UUID PRIMARY KEY,
    user_id             TEXT        NOT NULL,
    source_interview_id TEXT        NOT NULL,
    reason              TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_learning_plan_skip_source_interview UNIQUE (source_interview_id)
);

CREATE INDEX idx_learning_plan_skip_user ON learning_plan_skip (user_id, created_at DESC);
