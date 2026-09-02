#!/usr/bin/env bash
# ============================================================
# AI Video Studio - 关闭脚本（Linux / Git Bash）
# 功能：关闭本项目的 avs-* 服务容器，释放主机资源。
#       仅停止容器（不删除镜像、不删除数据）。
# 用法：./stop.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "===== 关闭 AI Video Studio ====="

echo "停止本项目容器 ..."
docker compose down

echo ""
echo "===== 已关闭 ====="
echo "本项目的所有容器已停止。"
echo "重新启动：./start.sh"
