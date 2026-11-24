# P5-J6 · 高可用演练手册（单机多实例）

> 2026-11-24 · 公开运行手册；不含密钥、题库原文、个人数据。
> 详细演练日志、原始计数与 RTO/RPO 记录在内部证据目录（`docs/private/`，不随仓库）。

## 1. 边界

- **单机多实例**，不是多可用区高可用；不承诺跨机容灾。
- PG 是**主 + 异步流复制从 + 手动提升**，不是自动 failover；RPO 不承诺 0。
- **不做应用层读写分离**：主库故障期间读写都失败，提升 + 重指后恢复。
- nginx 单点（生产用云 LB）；Qdrant 单点；uploads 是宿主共享卷（多机生产路径用对象存储）。
- HA 全量未在同一 8GB Docker 内同时起；按故障域 profile 分批演练。

## 2. 拓扑与 profile

`docker-compose.ha.yml`（项目名 `marda-ha`，独立网络与 `docker/volumes/ha/` 数据卷）：

| profile | 服务 | 用途 |
| --- | --- | --- |
| core（默认） | nginx、web、gateway×2、business×2、PG 主从、Redis 1 主 2 从 3 哨兵 | 网关 / 业务 / 数据层演练 |
| agent | api×2（1 uvicorn worker/容器）、Qdrant、embedding | 面试链路：锁 / SSE / checkpoint |
| events | Kafka×3（KRaft、6 分区、RF=3、minISR=2）、worker×2、relay×2、kafka-init | outbox / 消费 / 再均衡 |

静态 IP 是契约（`docker/nginx-ha/upstreams.conf`、`scripts/ha/drain.sh`、compose 的 IPAM 三处同步）。

## 3. 常用命令

```bash
# core
docker compose -f docker-compose.ha.yml up -d --build
# agent（面试链路）
docker compose -f docker-compose.ha.yml --profile agent up -d --build api-1 api-2 qdrant embedding
# events
PG_HOST=postgres-replica docker compose -f docker-compose.ha.yml --profile events up -d \
  kafka-1 kafka-2 kafka-3 kafka-init
PG_HOST=postgres-replica docker compose -f docker-compose.ha.yml --profile events up -d --no-deps \
  worker-1 worker-2 relay-1 relay-2
```

`PG_HOST` 默认 `postgres-primary`；PG 切换后所有应用用 `PG_HOST=postgres-replica` 重建。

## 4. 计划内摘流 / 恢复

```bash
bash scripts/ha/drain.sh gateway-1     # nginx upstream 标 down + reload + 等待
docker compose -f docker-compose.ha.yml stop gateway-1
bash scripts/ha/undrain.sh gateway-1
```

business 不经过 nginx：Spring graceful shutdown 把 readiness 变为 REFUSING_TRAFFIC，
gateway 的 2s 健康检查自动摘除；直接 `docker compose stop business-1` 即可。
api 同理（nginx 被动摘流 + `proxy_next_upstream`）。

## 5. 数据副本与故障演练

```bash
bash scripts/ha/seed.sh           # dev 三库 → HA 主库（drop/create/restore；行数对照）
bash scripts/ha/promote_pg.sh     # 停主 → 提升从库 → 应用重指 → RTO/RPO
```

- `seed.sh` 只走管道、不落 dump 文件；HA 卷在 `docker/volumes/ha/`（gitignore）。
- 旧主库在 `promote_pg.sh` 后保持停止；重启会变成第二主库，需重建 HA 卷再 seed。
- 演练后恢复被杀实例用 **`docker compose up -d --no-deps <svc>`**，不要用 `docker compose start`：
  `start` 会按 `depends_on` 把已停的旧 `postgres-primary` 拉起来（双主风险）。
- Redis 被 Sentinel 提升的副本**不要重启**：容器命令行里仍是 `--replicaof 172.31.240.80`，
  重启会按命令行回退成从库；生产用托管 Redis / 持久化配置。
- Redis / Kafka / worker / relay 的故障演练见第 7 节；`docker kill` 后容器不会自动重启（manual stop），
  演练结束用 `docker compose ... start <service>` 恢复。

## 6. 失败语义

| 组件 | 故障时 | 恢复 |
| --- | --- | --- |
| gateway | nginx 被动摘流 / 1s 连接超时 + 重试；GET 零 5xx | 实例恢复后自动回池 |
| business | gateway 健康检查 2s 内摘除 | 实例恢复后回池 |
| Redis | 缓存 / 限流 fail open；会话锁 **fail closed**（503） | Sentinel 切换后重试；TTL 30s + 心跳 |
| Kafka | outbox 积压；面试主链路不受影响 | broker / worker 恢复后补发 |
| PG 主库 | 读写都失败；readiness DOWN | 手动提升 + `PG_HOST` 重指 |
| relay | outbox 积压 | 重启后补发（租约 + 至少一次 + 消费幂等） |

## 7. 演练结果（2026-11-24）

| 演练 | 结果 |
| --- | --- |
| gateway kill | 40/40 200，最坏 ~1.0s（connect 1s + 重试）；drain 路径零 5xx |
| business kill | 40/40 200，另一实例接管 |
| agent 中途 kill | `stalled`；409 + `Retry-After: 30`；TTL 后续跑；不重复计分（answered=2 / answers=2 / report=1） |
| Redis 主故障 | Sentinel 检测 1.64s；应用跟随；首请求锁 503，重试恢复 |
| Kafka broker 宕机 | RF3/minISR2；事件 4.92s 建计划，零丢失；ISR 恢复 3 |
| worker 宕机 | 幸存者 1.4s 建计划；单成员接管 6 分区；重启回两成员 |
| PG 主库故障 | RTO 28.1s；RPO 探针保留（本次 0）；checkpoint / 报告 / 计划可读 |
| relay 停机 | 积压；启动后 1.37s 发布并建计划，零丢失 |
| 缓存全清 | `FLUSHDB` db1 后 MISS → HIT，回源 DB |

## 8. 测试与 CI

- `pytest`：1051 passed / 36 skipped；`gateway mvn verify` 27；`business mvn verify` 56。
- CI 增加 HA compose 配置校验（`docker compose ... config -q`，不起容器、不连端口）。
- HA 演练本身不进 CI（需要 Docker 资源与人为故障注入）；证据在内部目录。
