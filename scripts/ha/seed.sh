#!/usr/bin/env bash
# P5-J6：把 dev PostgreSQL 的三份数据复制到 HA 主库（演练用“正式库副本”）。
#
# 为什么用副本：PG 主从切换是破坏性演练（停主库、提升从库），绝不拿 dev / 正式库做。
# 数据落 docker/volumes/ha/postgres-primary（gitignore），不含题库原文进仓库；
# 本脚本只打印行数，不打印报告/简历内容。
#
# 用法：
#   bash scripts/ha/seed.sh
#
# 前置：dev compose 的 postgres 卷里已有数据；HA core 已启动。
# 脚本会停掉 HA 应用侧连接（pg_terminate_backend），应用会自动重连。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEV="docker compose -f $ROOT/docker-compose.yml -p marda"
HA="docker compose -f $ROOT/docker-compose.ha.yml -p marda-ha"

echo "== 确保 dev postgres 已启动 =="
$DEV up -d postgres >/dev/null
for _ in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' marda-postgres-1 2>/dev/null || true)
  [ "$status" = "healthy" ] && break
  sleep 1
done
[ "$status" = "healthy" ] || { echo "dev postgres 未 healthy：$status" >&2; exit 1; }

DATABASES="marda marda_checkpoints marda_business"
echo "== 校验 dev 库存在 =="
for db in $DATABASES; do
  exists=$($DEV exec -T postgres psql -U marda -d postgres -tAc \
    "select 1 from pg_database where datname='$db'" | tr -d '[:space:]')
  [ "$exists" = "1" ] || { echo "dev 缺少数据库 $db" >&2; exit 2; }
done

echo "== 复制到 HA 主库（drop / create / dump | restore） =="
for db in $DATABASES; do
  echo "-- $db"
  $HA exec -T postgres-primary psql -U marda -d postgres -v ON_ERROR_STOP=1 -c \
    "select pg_terminate_backend(pid) from pg_stat_activity where datname='$db' and pid <> pg_backend_pid();" >/dev/null
  $HA exec -T postgres-primary psql -U marda -d postgres -v ON_ERROR_STOP=1 -c \
    "drop database if exists $db;" >/dev/null
  $HA exec -T postgres-primary psql -U marda -d postgres -v ON_ERROR_STOP=1 -c \
    "create database $db owner marda;" >/dev/null
  $DEV exec -T postgres pg_dump -U marda -d "$db" --no-owner --no-privileges \
    | $HA exec -T postgres-primary psql -U marda -d "$db" -v ON_ERROR_STOP=1 >/dev/null
done

echo "== 等副本追平 =="
for _ in $(seq 1 60); do
  lag=$($HA exec -T postgres-primary psql -U marda -d postgres -tAc \
    "select coalesce(extract(epoch from replay_lag)::int, 0) from pg_stat_replication limit 1" | tr -d '[:space:]')
  [ -n "$lag" ] && [ "$lag" -le 1 ] && break
  sleep 1
done

echo "== 行数对照（dev vs HA primary vs HA replica） =="
for db in marda marda_business; do
  if [ "$db" = "marda" ]; then tables="users interviews answers reports"; else tables="learning_plan learning_task notification business_outbox"; fi
  for t in $tables; do
    a=$($DEV exec -T postgres psql -U marda -d "$db" -tAc "select count(*) from $t" | tr -d '[:space:]')
    b=$($HA exec -T postgres-primary psql -U marda -d "$db" -tAc "select count(*) from $t" | tr -d '[:space:]')
    c=$($HA exec -T postgres-replica psql -U marda -d "$db" -tAc "select count(*) from $t" | tr -d '[:space:]')
    printf '%-18s %-18s dev=%-8s primary=%-8s replica=%-8s\n' "$db" "$t" "$a" "$b" "$c"
  done
done
echo "seed 完成（数据在 HA 卷，未写仓库）"
