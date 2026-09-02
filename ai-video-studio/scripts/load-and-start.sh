#!/usr/bin/env bash
# 目标机脚本：离线加载镜像并启动。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d offline/images ]; then
  for tar in offline/images/*.tar; do
    [ -f "$tar" ] || continue
    echo "加载镜像: $(basename "$tar")"
    docker load -i "$tar"
  done
fi

if [ "${1:-}" = "--gpu" ]; then
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
else
  docker compose up -d
fi
sleep 8
docker compose ps
echo "对话界面: http://localhost:8003"
