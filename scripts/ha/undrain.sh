#!/bin/sh
# P5-J6：恢复接流（去掉 nginx upstream 的 down 标记 + reload）。
# 用法：scripts/ha/undrain.sh <gateway-1|gateway-2|api-1|api-2>
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
COMPOSE="docker compose -f $ROOT/docker-compose.ha.yml -p marda-ha"
UPSTREAMS="$ROOT/docker/nginx-ha/upstreams.conf"

target=${1:-}
if [ -z "$target" ]; then
  echo "用法: $0 <gateway-1|gateway-2|api-1|api-2>" >&2
  exit 2
fi

case "$target" in
  gateway-1) addr=172.31.240.11:8080 ;;
  gateway-2) addr=172.31.240.12:8080 ;;
  api-1)     addr=172.31.240.31:8000 ;;
  api-2)     addr=172.31.240.32:8000 ;;
  *) echo "未知目标：$target" >&2; exit 2 ;;
esac

if ! grep -q "server $addr" "$UPSTREAMS"; then
  echo "upstream 配置里找不到 server $addr：$UPSTREAMS" >&2
  exit 1
fi

perl -0pi -e "s/server \Q$addr\E down/server $addr/" "$UPSTREAMS"
$COMPOSE exec -T nginx nginx -t
$COMPOSE exec -T nginx nginx -s reload
echo "已恢复接流 $target（$addr）"
