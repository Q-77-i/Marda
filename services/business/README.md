# marda-business（P5-J3）

> Java 业务服务：学习计划 / 任务 / 通知。**不在面试关键路径上**——服务不可用时学习面板
> 降级为空态，面试 SSE / WebSocket 仍走 nginx → Python 直连。
>
> 版本线：JDK 21 + Spring Boot 3.3.5 MVC（不是 WebFlux）+ JdbcClient + Flyway +
> PostgreSQL 独立库 `marda_business`。虚拟线程只用于跑阻塞 JDBC；Hikari 池仍是 DB 并发上限。

## 跑起来

### 本地进程

```bash
# 1) 业务库（compose 的 postgres 已在 55432；已有数据卷不会自动建库）
docker exec marda-postgres-1 psql -U marda -d marda \
  -c 'CREATE DATABASE marda_business OWNER marda;'

# 2) 启动（SERVICE_TOKEN 必填，gateway 与 business 用同一个值）
export SERVICE_TOKEN='本地随机生成的内网令牌'
export DB_URL='jdbc:postgresql://localhost:55432/marda_business'
export DB_USER=marda
export DB_PASSWORD=marda
mvn spring-boot:run
```

- 默认端口 `8081`；`/actuator/health`、`/actuator/health/readiness` 公开。
- 其余端点要求 `X-Service-Token`；`/api/v1/**` 还要求 `X-User-Id`。
- 本服务**不直接接受用户 JWT**：浏览器 JWT 只到 gateway，gateway 注入用户头。
- 缓存默认关闭；本地要验缓存时先起 Redis，再加
  `BUSINESS_CACHE_ENABLED=true REDIS_HOST=127.0.0.1 REDIS_PORT=6379 REDIS_DB=1`。

### compose 叠加

```bash
# .env 需要 JWT_SECRET（与 Python 共用）和 SERVICE_TOKEN
docker compose -f docker-compose.yml -f docker-compose.p5.yml up -d --build
```

- `business` / `gateway` 只在内部网络，不发布宿主端口；对外入口仍是 nginx `8080`。
- `/api/v1/learning/**` → gateway → business；`/internal/v1/**` 不经过 nginx。
- 默认 `docker compose up` 不加载叠加文件，仍是纯 Python 形态。

## API

| 方法 / 路径 | 说明 |
| --- | --- |
| `POST /api/v1/learning/plans` | 从报告摘要建计划；`Idempotency-Key` 必须等于 `source_interview_id` |
| `GET /api/v1/learning/plans?status&page&page_size` | 计划列表（含任务） |
| `GET /api/v1/learning/plans/{id}` | 计划详情（含任务） |
| `PATCH /api/v1/learning/tasks/{id}` | 改状态 / 截止时间；必须带当前 `version` |
| `GET /api/v1/learning/today` | 今日任务（含逾期；默认 `Asia/Shanghai`） |
| `POST /api/v1/learning/reminders/test` | 写一条 `in_app` / `pending` 通知 |
| `POST /internal/v1/learning-plans/from-report` | 事件载荷建计划；service token + `X-User-Id` |
| `GET /internal/v1/learning-plans/{user_id}/next` | 内部读下一步建议；无计划返回空 |

- 错误信封：`{code, message, request_id, details?}`；中文 `message`，稳定英文 `code`。
- 越权统一 `404`；版本冲突 `409 VERSION_CONFLICT`；状态冲突 `409 STATE_CONFLICT`；
  幂等冲突 `409 IDEMPOTENCY_CONFLICT`；空短板 `422 NO_WEAK_DOMAINS`；
  行为面 `422 UNSUPPORTED_INTERVIEW_TYPE`。
- 契约：[docs/p5/contracts/learning-api.yaml](../../docs/p5/contracts/learning-api.yaml)。

## 关键设计

| 决策 | 做法 | 理由 |
| --- | --- | --- |
| 持久层 | JdbcClient + 显式 SQL | 本域难点是 `ON CONFLICT` / 条件 UPDATE / 原子完成，不是 CRUD |
| 建计划幂等 | `UNIQUE(source_interview_id)` + `ON CONFLICT DO NOTHING` + 请求哈希 | 并发安全；同 key 不同体 409 |
| 任务并发 | `version` 条件 UPDATE，不用 Hibernate `@Version` | 契约从 1 开始；条件更新可直接断言影响行数 |
| 计划完成 | `UPDATE ... WHERE status='active' AND NOT EXISTS(未完成任务)` | 原子，不需要计划级锁 |
| 任务状态机 | `todo → in_progress / done / skipped`；终态不可回退 | 纯函数单测；非法转换 409 |
| 计划生成 | 每个短板域 1 个任务；按分数升序排优先级；截止日今天 + i 天 | 事件只有域级 advice，不虚构资料 |
| 活跃计划 | 允许多个 active；`today` / `next` 合并 | 不悄悄归档用户旧计划 |
| 通知 | J3 只写 `pending` 行 | 投递 / 重试 / DLQ 是 J5 的 worker |
| 虚拟线程 | `spring.threads.virtual.enabled=true` | 阻塞 JDBC 不占平台线程；DB 并发仍受 Hikari 限制 |

## 缓存（P5-J4）

| Key | TTL | 失效 |
| --- | --- | --- |
| `learning:today:{user}:{date}` | 1m | 建计划 / 更新任务事务提交后删 |
| `learning:plan:{user}:{id}` | 5m | 更新任务事务提交后删 |

- **cache-aside**：读 miss → 查 DB → 回填；TTL ±10% 抖动，防集中过期。
- **单飞**：Redis `SET NX PX` + 双检；等锁 200ms，等不到回源但不回填。
- **失败语义**：Redis 异常一律回源 DB（`X-Cache: BYPASS`），不抛给业务；readiness 不依赖 Redis。
- **不设 LRU**：同一 Redis 存 Python 的会话锁；淘汰锁会破坏正确性。内存压力留 J6 拆独立实例。
- `X-Cache: HIT | MISS | BYPASS` 仅用于证据 / 调试，不是前端契约；
  Micrometer `cache.requests{cache,result}` 可从 `/actuator/metrics` 读。

## 测试

```bash
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home mvn -B verify
```

- 46 个用例：领域 17（计划工厂 / 状态机 / 幂等哈希）、Schema 1、API 21、事务回滚 1、
  缓存 6（命中 / 写后失效 / 用户隔离 / 故障回源 / 禁用回源 / 跨实例共享）。
- API / 事务用例用 Testcontainers `postgres:16-alpine` + Flyway；与 CI 同一路径。
- 覆盖：建计划、幂等重放/冲突、并发只建一个、PATCH 版本/状态/due_at、越权 404、
  今日/next、内部 201/200/204、提醒写入、虚拟线程机械断言。
- 虚拟线程探针是测试专用 controller + `business.security.permit-paths`；生产 permit 列表
  只有 actuator / error。

## J3 边界（后续阶段）

- **不碰** Python finalize / `agent_outbox` / Kafka / relay / `processed_event`（J5）；
- **不做** Redis 缓存 / Sentinel（J4 / J6）、通知投递与 DLQ（J5）、`user_preference`（J5）、
  `resource_url` 资料回填（J4 / J5）、OTel / Prometheus（J8）、多实例 / HA（J6）；
- 被 public `POST /plans` 不校验场次归属（J3 单用户演示；J5 事件路径为主后可收敛）；
- 用户删号 / 学习数据 erasure 留 J6。
