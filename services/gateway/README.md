# marda-gateway（P5-J2 骨架）

> J2 第一片：路由 + JWT 鉴权 + 请求头注入 + 健康检查 + 访问日志。限流、熔断、灰度还没接。
>
> **版本线**：JDK 21（Maven enforcer 强制）+ Spring Boot 3.3.5 + Spring Cloud Gateway 2023.0.3。
> **虚拟线程**：只用于访问日志的阻塞 I/O（`VirtualThreadConfig` / `AccessLogFilter`），
> 不用于 Netty 请求处理——gateway 是 WebFlux，虚拟线程对事件循环没有收益。
> J3 的 Spring MVC business 服务才用 `spring.threads.virtual.enabled=true` 跑阻塞 DB / HTTP。
> 本机 JDK 21 在 `/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home`。

## 跑起来

```bash
export JWT_SECRET='至少32字符的本地密钥'   # MVP 与 Python 共用 HS256 secret
export SERVICE_TOKEN='内网 service token'  # 下发给 business，用户 JWT 不透传
export BUSINESS_URL='http://localhost:8081' # business 服务（J3 才会有）
mvn spring-boot:run
```

- `GET /actuator/health`：公开；
- `GET /api/v1/learning/**`：需要 `Authorization: Bearer <JWT>`，HS256，`sub` = user_id；
- 网关把 `X-User-Id` 设为 JWT `sub`，并剥离客户端自带的 `X-User-Id`、`Authorization`；
- 下发 `X-Service-Token`（客户端自带值被覆盖），business 只信内网 service token；
- 缺省注入 `X-Request-Id`、`traceparent`；客户端已有则不覆盖；
- 其余路径返回 401 / 404（MVP 只接 `/api/v1/learning/**`）。

## 测试

```bash
mvn test
```

4 个用例：健康检查公开、无 token 401、坏 token 401、有效 token 转发到 Mock 下游并带
`X-User-Id` / `X-Service-Token` / `X-Request-Id` / `traceparent`（客户端伪造的
`X-User-Id` / `X-Service-Token` 被覆盖，`Authorization` 不透传）。

## 还没做（J2 后续 / J3）

- Redis 令牌桶限流（`RequestRateLimiter`，按 user + IP）；
- Resilience4j 熔断 / 超时 / 舱壁；
- 灰度（header / user hash 权重）；
- `/actuator/prometheus` 的暴露策略与告警；
- 构建镜像与 compose 叠加（默认 `docker-compose.yml` 保持纯 Python）。
