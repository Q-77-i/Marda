package com.marda.business;

import com.marda.business.cache.BusinessCacheProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/**
 * P5-J3 业务服务：学习计划 / 任务 / 通知。
 *
 * <p>面试 SSE / WebSocket 不经过这里；本服务不在面试关键路径上，故障时学习面板降级为空态。
 */
@SpringBootApplication
@EnableConfigurationProperties(BusinessCacheProperties.class)
public class BusinessApplication {

    public static void main(String[] args) {
        SpringApplication.run(BusinessApplication.class, args);
    }
}
