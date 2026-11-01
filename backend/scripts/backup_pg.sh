#!/usr/bin/env bash
# PG 备份（P4-E5）：业务库 + checkpoint 库 → data/backups/，保留最近 7 份。
#
# 用法（本机 compose：用容器内 pg_dump，版本与 server 一致）：
#   PG_DUMP_MODE=docker BACKUP_DIR=data/backups bash backend/scripts/backup_pg.sh
#
# 外部 PG（宿主机 pg_dump 版本必须不高于 server）：
#   DATABASE_URL=postgresql://marda:marda@localhost:55432/marda \
#   CHECKPOINT_DATABASE_URL=postgresql://marda:marda@localhost:55432/marda_checkpoints \
#   BACKUP_DIR=../data/backups bash scripts/backup_pg.sh
#
# 恢复演练（**要真做过**，不然备份等于没有）：
#   docker compose exec -T postgres createdb -U marda marda_restore
#   cat data/backups/marda_YYYYmmdd-HHMMSS.dump | \
#     docker compose exec -T postgres pg_restore -U marda -d marda_restore
#   pytest -m postgres        # 集成测试指向恢复库跑一遍
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-../data/backups}"
KEEP="${KEEP:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PG_DUMP_MODE="${PG_DUMP_MODE:-host}"
PG_SERVICE="${PG_SERVICE:-postgres}"
PG_USER="${PG_USER:-marda}"
mkdir -p "$BACKUP_DIR"

dump_one() {
  local name="$1" url="$2" db="${3:-}"
  local out="${BACKUP_DIR}/${name}_${STAMP}.dump"
  if [ "$PG_DUMP_MODE" = "docker" ]; then
    [ -n "$db" ] || { echo "跳过 $name（docker 模式缺 db 名）"; return 0; }
    echo "备份 $name → $out（容器 $PG_SERVICE 内 pg_dump）"
    docker compose --project-directory "$REPO_ROOT" -f "$REPO_ROOT/docker-compose.yml" \
      exec -T "$PG_SERVICE" pg_dump -U "$PG_USER" --format=custom --no-owner --no-acl -d "$db" > "$out"
  else
    if [ -z "$url" ]; then
      echo "跳过 $name（未配置连接串）"
      return 0
    fi
    echo "备份 $name → $out"
    pg_dump --format=custom --no-owner --no-acl "$url" > "$out"
  fi
  echo "  $(du -h "$out" | cut -f1)"
}

if [ "$PG_DUMP_MODE" = "docker" ]; then
  dump_one "marda" "" "marda"
  dump_one "marda_checkpoints" "" "marda_checkpoints"
else
  dump_one "marda" "${DATABASE_URL:-}"
  dump_one "marda_checkpoints" "${CHECKPOINT_DATABASE_URL:-}"
fi

# 题库 SQLite 单独备份（不迁 PG 的那部分资产）
if [ -n "${QUESTION_DB_PATH:-}" ] && [ -f "${QUESTION_DB_PATH}" ]; then
  out="${BACKUP_DIR}/questions_${STAMP}.sqlite3"
  echo "备份题库 → $out"
  sqlite3 "${QUESTION_DB_PATH}" ".backup '${out}'"
fi

# 只保留最近 KEEP 份
ls -1t "${BACKUP_DIR}"/*_*.dump "${BACKUP_DIR}"/*.sqlite3 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  echo "清理旧备份：$old"
  rm -f "$old"
done

echo "完成。备份目录：$BACKUP_DIR"
