package com.marda.business.outbox;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** 发布端口：返回每个 outbox 行的结果（null = 成功，字符串 = 错误）。 */
public interface EventPublisher {

    Map<UUID, String> publishBatch(List<OutboxRow> rows);
}
