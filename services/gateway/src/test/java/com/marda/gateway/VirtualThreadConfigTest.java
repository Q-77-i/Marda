package com.marda.gateway;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.concurrent.ExecutorService;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(classes = VirtualThreadConfig.class)
class VirtualThreadConfigTest {

    @Autowired
    ExecutorService gatewayBlockingExecutor;

    @Test
    void executor_runs_on_virtual_threads() throws Exception {
        assertThat(gatewayBlockingExecutor.submit(() -> Thread.currentThread().isVirtual()).get())
                .isTrue();
    }
}
