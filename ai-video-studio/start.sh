#!/usr/bin/env bash
# ============================================================
# AI Video Studio - 启动脚本（Linux / Git Bash）
# 功能：先停止 Docker 中所有其他项目的容器以释放主机资源，
#       再启动本项目的 avs-* 服务。
# 用法：./start.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PROJECT_PREFIX="avs-"

echo ""
echo "===== AI Video Studio 启动 ====="

# ---------- 1. 停止其他项目的容器（释放资源） ----------
echo ""
echo "[1/3] 检查并停止其他项目的容器 ..."
OTHERS=$(docker ps --format '{{.Names}}' | grep -v "^${PROJECT_PREFIX}" || true)
if [ -n "$OTHERS" ]; then
  echo "  将停止以下容器（属于其他项目）："
  echo "$OTHERS" | sed 's/^/    - /'
  echo "$OTHERS" | xargs -r docker stop
  echo "  已停止 $(echo "$OTHERS" | wc -l | tr -d ' ') 个其他容器"
else
  echo "  没有发现其他项目的容器在运行"
fi

# ---------- 2. 启动本项目服务 ----------
echo ""
echo "[2/3] 启动 AI Video Studio 服务 ..."
docker compose up -d

# ---------- 3. 等待并检查状态 ----------
echo ""
echo "[3/3] 等待服务就绪 ..."
if command -v sleep >/dev/null 2>&1; then
  sleep 8
else
  echo "  (未找到 sleep 命令，跳过等待)"
fi
docker compose ps

echo ""
echo "===== 启动完成 ====="
echo "  对话界面 : http://localhost:8003"
echo "  理解服务 : http://localhost:8001"
echo "  执行服务 : http://localhost:8002"
echo ""
echo "提示：用 ./stop.sh 可一键关闭本项目的所有服务。"
