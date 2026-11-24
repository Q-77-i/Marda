-- P5-J6：HA 主库的复制账号（数据卷首次初始化时执行）。
--
-- 只建角色；pg_hba 的 replication 规则由挂载的 docker/postgres-ha/pg_hba.conf 提供
-- （官方镜像默认的 `host all all` 不匹配 replication 伪库，必须单独一行）。
-- 密码默认 replicator；副本启动脚本会用 REPLICATION_PASSWORD 再 ALTER 一次，
-- 保证 compose 环境变量改了也不会出现「角色建了但密码不对」。
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN
    CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator';
  END IF;
END
$$;
