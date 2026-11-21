package com.marda.business.plan.application;

import com.marda.business.common.BadRequestException;
import com.marda.business.event.EventEnvelopeReader;
import com.marda.business.event.NonRetryableEventException;
import com.marda.business.event.ParsedEvent;
import com.marda.business.inbox.ProcessedEventRepository;
import com.marda.business.plan.domain.InvalidPlanInputException;
import com.marda.business.plan.web.FromReportRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.DltHandler;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.annotation.RetryableTopic;
import org.springframework.kafka.retrytopic.SameIntervalTopicReuseStrategy;
import org.springframework.retry.annotation.Backoff;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;

/**
 * `interview.finalized` 消费者：报告收尾 → 建学习计划。
 *
 * <p>幂等：`processed_event` 与计划写入同一事务；计划本身还有 source_interview_id 唯一键。
 * 重试：指数退避 + retry topic，5 次后 DLQ；结构 / 校验错误（毒消息）不重试，直送 DLQ。
 */
@Component
@ConditionalOnProperty(name = "business.worker.enabled", havingValue = "true")
public class InterviewFinalizedConsumer {

    static final String CONSUMER = "business-plan";
    private static final Logger log = LoggerFactory.getLogger(InterviewFinalizedConsumer.class);

    private final EventEnvelopeReader reader;
    private final ProcessedEventRepository processed;
    private final LearningPlanService plans;
    private final Clock clock;

    public InterviewFinalizedConsumer(
            EventEnvelopeReader reader,
            ProcessedEventRepository processed,
            LearningPlanService plans,
            Clock clock) {
        this.reader = reader;
        this.processed = processed;
        this.plans = plans;
        this.clock = clock;
    }

    @KafkaListener(
            topics = "interview.finalized",
            groupId = "${business.kafka.plan-consumer-group:business-plan}")
    @RetryableTopic(
            attempts = "5",
            backoff = @Backoff(delay = 1000, multiplier = 2.0),
            retryTopicSuffix = ".retry",
            dltTopicSuffix = ".dlq",
            sameIntervalTopicReuseStrategy = SameIntervalTopicReuseStrategy.SINGLE_TOPIC,
            exclude = NonRetryableEventException.class,
            autoCreateTopics = "true")
    @Transactional
    public void onMessage(String message) {
        ParsedEvent<FromReportRequest> event = reader.read(
                message, "interview.finalized", FromReportRequest.class);
        if (!processed.markProcessed(event.eventId(), CONSUMER, clock.instant())) {
            log.info("重复事件直接 ack event_id={}", event.eventId());
            return;
        }
        try {
            plans.create(event.payload().toPlanInput(), event.payload().interviewId(), true);
        } catch (InvalidPlanInputException | BadRequestException exception) {
            throw new NonRetryableEventException("interview.finalized 业务校验失败", exception);
        }
    }

    @DltHandler
    public void onDlt(String message) {
        // 事件本身不合法 / 计划输入不合法：人工查看后修 payload 再 replay。
        log.error("interview.finalized 进入 DLQ：{}", message);
    }
}
