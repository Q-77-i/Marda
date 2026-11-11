-- P5-J3：Java 业务库（与 Python 业务库 / checkpoint 库分开）。
--
-- 只在数据卷**首次初始化**时执行（PG 官方镜像的 initdb.d 机制）。
-- 已有数据卷不会重跑；需要手动：
--   docker exec marda-postgres-1 psql -U marda -d marda -c 'CREATE DATABASE marda_business OWNER marda;'
CREATE DATABASE marda_business OWNER marda;
