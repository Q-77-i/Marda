#!/bin/sh
# P5-J6：计划内摘流（nginx upstream 标 down + reload）。
#
# 用法：
#   scripts/ha/drain.sh gateway-1      # 之后由调用方 docker compose stop gateway-1
#   scripts/ha/undrain.sh gateway-1    # 恢复接流
#
# 为什么需要它：nginx 开源版只有被动健康检查，没有 Plus 的主动探活；计划内维护
# 如果不先摘流，第一个打到停止中实例的请求会 502（GET 可被动重试，POST 不会）。
# 标 down 后 nginx 会立刻跳过该实例，等在途请求跑完再停容器。
#
# business-1/2 不经过 nginx：它们由 gateway 的 HealthCheck 负载均衡摘除
# （Spring graceful shutdown 会把 readiness 变成 REFUSING_TRAFFIC，2s 内生效），
# 直接 docker stop 即可。
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
COMPOSE="docker compose -f $ROOT/docker-compose.ha.yml -p marda-ha"
UPSTREAMS="$ROOT/docker/nginx-ha/upstreams.conf"

target=${1:-}
if [ -z "$target" ]; then
  echo "用法: $0 <gateway-1|gateway-2|api-1|api-2|business-1|business-2>" >&2
  exit 2
fi

case "$target" in
  gateway-1) addr=172.31.240.11:8080 ;;
  gateway-2) addr=172.31.240.12:8080 ;;
  api-1)     addr=172.31.240.31:8000 ;;
  api-2)     addr=172.31.240.32:8000 ;;
  business-1|business-2)
    echo "$target 不经过 nginx；Spring graceful shutdown + gateway readiness 摘流，"
    echo "直接执行：$COMPOSE stop $target"
    exit 0
    ;;
  *) echo "未知目标：$target" >&2; exit 2 ;;
esac

if ! grep -q "server $addr" "$UPSTREAMS"; then
  echo "upstream 配置里找不到 server $addr：$UPSTREAMS" >&2
  exit 1
fi

perl -0pi -e "s/server \Q$addr\E(?! down)/server $addr down/" "$UPSTREAMS"
$COMPOSE exec -T nginx nginx -t
$COMPOSE exec -T nginx nginx -s reload

wait_s=${DRAIN_WAIT_S:-5}
echo "已摘流 $target（$addr），等待 ${wait_s}s 让在途请求完成…"
sleep "$wait_s"
echo "现在可以停止实例：$COMPOSE stop $target"
