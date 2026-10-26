#!/usr/bin/env bash
# PG 备份（P4-E5）：业务库 + checkpoint 库 → data/backups/，保留最近 7 份。
#
# 用法：
#   DATABASE_URL=postgresql://marda:marda@postgres:5432/marda \
#   CHECKPOINT_DATABASE_URL=postgresql://marda:marda@postgres:5432/marda_checkpoints \
#   BACKUP_DIR=../data/backups ./scripts/backup_pg.sh
#
# 恢复演练（**要真做过**，不然备份等于没有）：
#   createdb marda_restore && pg_restore -d marda_restore data/backups/marda_YYYYmmdd-HHMMSS.dump
#   pytest -m postgres        # 集成测试指向恢复库跑一遍
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-../data/backups}"
KEEP="${KEEP:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

dump_one() {
  local name="$1" url="$2"
  if [ -z "$url" ]; then
    echo "跳过 $name（未配置连接串）"
    return 0
  fi
  local out="${BACKUP_DIR}/${name}_${STAMP}.dump"
  echo "备份 $name → $out"
  pg_dump --format=custom --no-owner --no-acl "$url" > "$out"
  echo "  $(du -h "$out" | cut -f1)"
}

dump_one "marda" "${DATABASE_URL:-}"
dump_one "marda_checkpoints" "${CHECKPOINT_DATABASE_URL:-}"

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
