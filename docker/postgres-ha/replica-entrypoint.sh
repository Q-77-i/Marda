#!/bin/sh
# P5-J6：PG 流复制副本启动脚本（单机 HA 演练）。
#
# 首次启动用 pg_basebackup 从主库拉一份基线并写 standby.signal（-R）；
# 之后交给官方 entrypoint 启动（standby 持续回放 WAL）。
#
# 注意：本脚本由 compose 以 `/bin/sh /ha/replica-entrypoint.sh` 调用，
# **不依赖文件执行位**（挂载进来的脚本常常是 644；chmod 不可用时也能跑）。
# 特权处理交给官方 docker-entrypoint.sh：root 启动 → chown → 降权到 postgres。
set -eu

mkdir -p "$PGDATA"
chown -R postgres:postgres "$PGDATA"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[ha-replica] waiting for primary $PRIMARY_HOST ..."
  until su-exec postgres pg_isready -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER" >/dev/null 2>&1; do
    sleep 1
  done

  # 确保复制账号存在且密码与 compose 环境一致（主库如未跑 init SQL 也能自愈）
  echo "[ha-replica] ensuring replication role $REPLICATION_USER ..."
  su-exec postgres env PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h "$PRIMARY_HOST" -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$REPLICATION_USER') THEN CREATE ROLE $REPLICATION_USER WITH REPLICATION LOGIN PASSWORD '$REPLICATION_PASSWORD'; ELSE ALTER ROLE $REPLICATION_USER WITH REPLICATION LOGIN PASSWORD '$REPLICATION_PASSWORD'; END IF; END \$\$;"

  # pg_basebackup -R 写的 primary_conninfo 不含密码；用 .pgpass 让副本重启后
  # 仍能回连主库（否则首启成功、重启即认证失败——演练时会误判成「复制坏了」）。
  mkdir -p /var/lib/postgresql
  printf '%s:5432:replication:%s:%s\n' \
    "$PRIMARY_HOST" "$REPLICATION_USER" "$REPLICATION_PASSWORD" > /var/lib/postgresql/.pgpass
  chown postgres:postgres /var/lib/postgresql/.pgpass
  chmod 600 /var/lib/postgresql/.pgpass

  echo "[ha-replica] base backup from $PRIMARY_HOST"
  su-exec postgres env HOME=/var/lib/postgresql PGPASSWORD="$REPLICATION_PASSWORD" \
    pg_basebackup -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER" \
      -D "$PGDATA" -Fp -Xs -P -R
  chown -R postgres:postgres "$PGDATA"
fi

exec docker-entrypoint.sh postgres -c hot_standby=on -c max_connections=200 -c hba_file=/ha/pg_hba.conf
