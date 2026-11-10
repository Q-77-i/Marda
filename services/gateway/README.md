# marda-gateway（P5-J2）

> J2 已完成：路由 + JWT 鉴权 + 请求头注入 + 健康检查 + 访问日志 + **Redis 令牌桶限流** +
> **Resilience4j 熔断 / 超时 / 舱壁** + **user hash 灰度** + **CI Java job**。
> SSE / WebSocket 仍走 nginx → Python，不经过网关。
>
> **版本线**：JDK 21（Maven enforcer 强制）+ Spring Boot 3.3.5 + Spring Cloud Gateway 2023.0.3 +
> Resilience4j 2.1.0。**虚拟线程**只用于访问日志的阻塞 I/O（`VirtualThreadConfig` / `AccessLogFilter`），
> 不用于 Netty 请求处理——gateway 是 WebFlux，虚拟线程对事件循环没有收益。
> J3 的 Spring MVC business 服务才用 `spring.threads.virtual.enabled=true` 跑阻塞 DB / HTTP。
> 本机 JDK 21 在 `/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home`。

## 跑起来

```bash
export JWT_SECRET='至少32字符的本地密钥'   # MVP 与 Python 共用 HS256 secret
export SERVICE_TOKEN='内网 service token'  # 下发给 business；也用于灰度 header 的内部令牌
export BUSINESS_URL='http://localhost:8081' # business 服务（J3 才会有）
# 可选：REDIS_HOST / REDIS_PORT（默认 localhost:6379，compose 里的 Redis 即可）
# 可选：BUSINESS_CANARY_URL + GATEWAY_CANARY_PERCENT（灰度目标与百分比）
mvn spring-boot:run
```

- `GET /actuator/health`：公开；Redis 挂时**仍为 UP**（限流 fail open，不能让编排层摘掉整个网关）。
- `GET /api/v1/learning/**`：需要 `Authorization: Bearer <JWT>`，HS256，`sub` = user_id。
- 网关把 `X-User-Id` 设为 JWT `sub`，剥离客户端自带的 `X-User-Id`、`Authorization`。
- 下发 `X-Service-Token`（客户端自带值被覆盖），business 只信内网 service token。
- 缺省注入 `X-Request-Id`、`traceparent`；客户端已有则不覆盖。
- 其余路径返回 401 / 404（MVP 只接 `/api/v1/learning/**`）。

## 失败语义

| 场景 | 行为 |
| --- | --- |
| 超过令牌桶（默认 1 token/s + burst 20） | `429` + `Retry-After: 1` + `{"code":"RATE_LIMITED",...}` + `X-RateLimit-*`；不打下下游 |
| Redis 不可用 / 超时（200ms 上界） | **fail open**：放行；`/actuator/health` 仍 UP；计数器 `gateway.rate_limit.redis_timeout` |
| 下游连续 5xx（默认 20 次窗口 / 至少 10 次调用 / 50%） | 熔断打开；后续请求 `503` + `DEPENDENCY_UNAVAILABLE`，不再打下下游 |
| 下游超过 2s（Resilience4j TimeLimiter） | `503` + `DEPENDENCY_UNAVAILABLE` |
| 舱壁满（默认 20 并发/实例） | `429` + `RATE_LIMITED`；舱壁在熔断外层，本地降载不记成下游失败 |
| 灰度 | 按 `SHA-256(salt + user)` 分桶；`X-Canary: stable\|canary` 仅在 `GATEWAY_CANARY_HEADER_ENABLED=true` **且**带匹配的 `X-Canary-Token`（= `SERVICE_TOKEN`）时生效 |

- 熔断 `statusCodes` 覆盖标准 5xx 全集（500–511，跳过非标准 509）。
- 熔断 / 舱壁 / 灰度都是**每路由一份**：当前只有 `learning` 一条路由，稳定版与灰度版共享同一个熔断器。
- 熔断器健康指标已关闭：某下游熔断不代表网关自身不能服务；下游可用性由 J8 监控单独看。
- `/actuator/prometheus` 本片**不暴露**（exposure 只有 health/info）；限流 / 熔断计数器已埋，J8 再连同抓取策略一起开。
- `GATEWAY_TRUST_FORWARDED_FOR=true` 时按 `X-Forwarded-For` 第一段做 IP 限流；默认只信连接对端。

## 测试

```bash
# 本机 Maven 默认可能不是 JDK 21，必须显式指定 JAVA_HOME
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home mvn -B verify
```

- 24 个用例：健康检查公开、无 token / 坏 token 401、请求头注入与伪造覆盖；
  限流 429 / `Retry-After` / 错误信封 / 用户隔离、Redis 挂 fail open 与健康保持 UP；
  熔断 5xx 打开 / 半开恢复 / 超时、舱壁并发拒绝且不计入熔断；灰度 100% / header 内部令牌 / 防伪造 / 分桶确定性。
- 限流用例用 Testcontainers 起 `redis:7-alpine`（本机与 CI 同一条路径，需要 Docker）。
- CI：`.github/workflows/ci.yml` 的 `java-gateway` job 跑 `mvn -B verify`。

## 还没做（后续阶段）

- 幂等重试（GET 重试 / POST `Idempotency-Key`）：随 J3 业务 API 一起做；
- 优雅下线（`server.shutdown: graceful` + 摘流）：J6 多实例 / HA；
- OpenAPI schema diff、网关 p95 < 5ms @ 500 RPS、杀实例故障演练：J7 / J9；
- 灰度异常自动回切：J9，需要错误率指标闭环；
- 构建镜像与 compose 叠加：J3/J6，默认 `docker-compose.yml` 保持纯 Python。
