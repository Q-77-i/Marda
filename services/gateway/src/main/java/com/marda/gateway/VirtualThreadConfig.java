package com.marda.gateway;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@Configuration
public class VirtualThreadConfig {

    /**
     * 只给阻塞 I/O（访问日志等）用；不要拿它跑 Netty 请求处理——
     * gateway 的请求路径是 WebFlux，虚拟线程对事件循环没有收益。
     */
    @Bean(destroyMethod = "close")
    ExecutorService gatewayBlockingExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }
}
