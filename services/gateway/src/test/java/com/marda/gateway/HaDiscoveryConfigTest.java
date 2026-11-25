package com.marda.gateway;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.cloud.gateway.route.RouteDefinition;
import org.springframework.cloud.gateway.route.RouteDefinitionLocator;
import org.springframework.core.env.Environment;

import java.net.URI;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * J6-A：HA 路由的配置契约。
 *
 * <p>不起真实 business：只证明两件事——静态实例列表真的进了 DiscoveryClient，
 * 路由 URI 真的是 lb://business。真实的健康摘流由 HA 演练验证（需要容器与杀进程），
 * 不塞进单测。
 */
@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {
                "gateway.jwt.secret=test-secret-0123456789abcdef0123456789",
                "gateway.service-token=test-service-token",
                "gateway.rate-limit.enabled=false",
                "BUSINESS_URI=lb://business",
                "spring.cloud.discovery.client.simple.instances.business[0].uri=http://business-1:8081",
                "spring.cloud.discovery.client.simple.instances.business[1].uri=http://business-2:8081"
        })
class HaDiscoveryConfigTest {

    @Autowired
    DiscoveryClient discoveryClient;

    @Autowired
    RouteDefinitionLocator routeDefinitionLocator;

    @Autowired
    Environment environment;

    @Test
    void business_has_two_static_instances() {
        List<ServiceInstance> instances = discoveryClient.getInstances("business");
        assertThat(instances).hasSize(2);
        assertThat(instances)
                .extracting(ServiceInstance::getUri)
                .containsExactlyInAnyOrder(
                        URI.create("http://business-1:8081"),
                        URI.create("http://business-2:8081"));
    }

    @Test
    void learning_route_uses_load_balancer() {
        RouteDefinition learning = routeDefinitionLocator.getRouteDefinitions()
                .filter(route -> "learning".equals(route.getId()))
                .blockFirst();
        assertThat(learning).isNotNull();
        assertThat(learning.getUri()).isEqualTo(URI.create("lb://business"));
    }

    @Test
    void graceful_shutdown_is_configured() {
        assertThat(environment.getProperty("server.shutdown")).isEqualTo("graceful");
        assertThat(environment.getProperty("spring.lifecycle.timeout-per-shutdown-phase"))
                .isEqualTo("30s");
    }
}
