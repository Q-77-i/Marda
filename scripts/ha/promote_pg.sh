#!/usr/bin/env bash
# P5-J6：PG 主库故障演练（停主 → 手动提升从库 → 应用重指）。
#
# 边界（必须如实写进证据）：
# - 单机 compose 的 PG 主从，不是多可用区；提升是**手动**的，不是自动 failover；
# - 异步流复制：RPO 不承诺为 0，本脚本在停主前写一行探针并等待 replay_lag≤1s；
# - 不做应用层读写分离：主库故障期间读写都失败，提升 + 重指后恢复；
# - 旧主库保持停止（重启会变成第二主库）；演练后重建用 scripts/ha/seed.sh。
#
# 用法：
#   bash scripts/ha/promote_pg.sh
#
# 前提：HA 主从均在 streaming；应用已起（api/business/worker/relay 视 profile）。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
HA="docker compose -f $ROOT/docker-compose.ha.yml -p marda-ha"

echo "== 0. 停主前：写 RPO 探针并等副本追平 =="
$HA exec -T postgres-primary psql -U marda -d marda -v ON_ERROR_STOP=1 -c "
create table if not exists ha_rpo_probe(ts timestamptz not null default now(), note text);
insert into ha_rpo_probe(note) values ('before-failover');" >/dev/null
for _ in $(seq 1 30); do
  lag=$($HA exec -T postgres-primary psql -U marda -d postgres -tAc \
    "select coalesce(extract(epoch from replay_lag)::int, 0) from pg_stat_replication limit 1" | tr -d '[:space:]')
  [ -n "$lag" ] && [ "$lag" -le 1 ] && break
  sleep 1
done
echo "replay_lag=${lag:-unknown}s"

echo "== 1. 停主库 =="
t0=$(python3 -c 'import time; print(time.time())')
$HA stop postgres-primary >/dev/null
echo "postgres-primary stopped"

echo "== 2. 提升从库 =="
$HA exec -T -u postgres postgres-replica pg_ctl promote -D /var/lib/postgresql/data
for _ in $(seq 1 30); do
  recovery=$($HA exec -T postgres-replica psql -U marda -d postgres -tAc "select pg_is_in_recovery()" | tr -d '[:space:]')
  [ "$recovery" = "f" ] && break
  sleep 1
done
[ "$recovery" = "f" ] || { echo "副本未完成提升" >&2; exit 1; }
echo "postgres-replica promoted"

echo "== 3. 应用重指到新主库（PG_HOST=postgres-replica） =="
# --no-deps：旧主库已停，不能再去等 postgres-primary: service_healthy。
PG_HOST=postgres-replica $HA --profile agent --profile events up -d --no-deps --force-recreate \
  business-1 business-2 api-1 api-2 >/dev/null
# 事件侧若在跑，也一并重指（没跑就跳过，避免把 events profile 意外拉起）
running=$($HA --profile events ps --status running --services 2>/dev/null || true)
if echo "$running" | grep -q '^worker-1$'; then
  PG_HOST=postgres-replica $HA --profile events up -d --no-deps --force-recreate worker-1 worker-2 >/dev/null
fi
if echo "$running" | grep -q '^relay-1$'; then
  PG_HOST=postgres-replica $HA --profile events up -d --no-deps --force-recreate relay-1 relay-2 >/dev/null
fi

echo "== 4. 等应用 readiness =="
ready=0
for _ in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8281/actuator/health/readiness || true)
  [ "$code" = "200" ] && { ready=1; break; }
  sleep 1
done
t1=$(python3 -c 'import time; print(time.time())')
if [ "$ready" != "1" ]; then
  echo "应用未恢复 readiness（可能仍在启动）；请查看 business-1 日志" >&2
  exit 2
fi
python3 - "$t0" "$t1" <<'PY'
import sys
print(f"RTO（停主 → business readiness 200）= {float(sys.argv[2]) - float(sys.argv[1]):.1f}s")
PY

echo "== 4b. 等 agent readiness（checkpoint 库也在新主库） =="
for p in 8101 8102; do
  ok=0
  for _ in $(seq 1 90); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$p/readyz" || true)
    [ "$code" = "200" ] && { ok=1; break; }
    sleep 1
  done
  [ "$ok" = "1" ] || { echo "api:${p} 未恢复 readyz" >&2; exit 2; }
done
echo "agent readiness ok"

echo "== 5. RPO 探针 =="
$HA exec -T postgres-replica psql -U marda -d marda -c "select count(*) as rpo_probe_rows from ha_rpo_probe;"
echo "== 6. 数据与报告可读性（真实数据副本） =="
uid=$($HA exec -T postgres-replica psql -U marda -d marda -tAc "select id from users limit 1" | tr -d '[:space:]')
echo "user=$uid"
echo "提示：旧主库保持停止；如需恢复原形态，重建 HA 数据卷并重跑 scripts/ha/seed.sh。"
