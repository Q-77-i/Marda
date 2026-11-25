package com.marda.business;

import com.marda.business.cache.BusinessCacheProperties;
import com.marda.business.outbox.BusinessOutboxProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * P5-J3 业务服务：学习计划 / 任务 / 通知。
 *
 * <p>面试 SSE / WebSocket 不经过这里；本服务不在面试关键路径上，故障时学习面板降级为空态。
 * P5-J5 起同一个 jar 可跑 worker 角色（{@code business.worker.enabled=true}）：
 * outbox relay + 事件消费者；web 角色不装配这些 bean。
 */
@SpringBootApplication
@EnableScheduling
@EnableConfigurationProperties({BusinessCacheProperties.class, BusinessOutboxProperties.class})
public class BusinessApplication {

    public static void main(String[] args) {
        SpringApplication.run(BusinessApplication.class, args);
    }
}
