package com.marda.business.event;

import java.util.UUID;

/** 解析并校验后的事件：event_id 供 processed_event 去重，payload 是类型化载荷。 */
public record ParsedEvent<T>(UUID eventId, T payload) {
}
