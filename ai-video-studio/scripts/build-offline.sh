#!/usr/bin/env bash
# 构建机脚本：把依赖、模型、镜像全部缓存到本文件夹，供目标机离线部署。
# 用法: ./scripts/build-offline.sh [-m qwen2.5vl:3b] [--include-7b] [--skip-whisper]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VLM_MODEL="qwen2.5vl:3b"
INCLUDE_7B=0
SKIP_WHISPER=0
while [ $# -gt 0 ]; do
  case "$1" in
    -m|--model) VLM_MODEL="$2"; shift 2 ;;
    --include-7b) INCLUDE_7B=1; shift ;;
    --skip-whisper) SKIP_WHISPER=1; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

mkdir -p offline/wheels offline/images models/ollama models/whisper

echo "[1/5] 缓存 pip 依赖（Linux wheel）..."
docker pull python:3.12-slim
docker run --rm \
  -v "${ROOT}/offline/wheels:/wheels" \
  -v "${ROOT}/services:/services:ro" \
  python:3.12-slim sh -c \
  "pip download -r /services/analyzer/requirements.txt -d /wheels && \
   pip download -r /services/executor/requirements.txt -d /wheels && \
   pip download -r /services/planner/requirements.txt -d /wheels"

echo "[2/5] 拉取 Ollama 镜像并预下载模型..."
docker pull ollama/ollama
docker rm -f avs-build-ollama 2>/dev/null || true
docker run -d --name avs-build-ollama -v "${ROOT}/models/ollama:/root/.ollama" ollama/ollama
docker exec avs-build-ollama ollama pull "${VLM_MODEL}"
if [ "${INCLUDE_7B}" = "1" ]; then docker exec avs-build-ollama ollama pull qwen2.5vl:7b; fi
docker stop avs-build-ollama
docker rm avs-build-ollama

if [ "${SKIP_WHISPER}" = "0" ]; then
  echo "[3/5] 缓存 faster-whisper small 模型..."
  docker run --rm \
    -v "${ROOT}/offline/wheels:/wheels" \
    -v "${ROOT}/models/whisper:/models/whisper" \
    python:3.12-slim sh -c \
    "pip install --no-index --find-links=/wheels faster-whisper && python -c \"from faster_whisper import download_model; download_model('small', output_dir='/models/whisper')\""
else
  echo "[3/5] 跳过 whisper 模型"
fi

echo "[4/5] 构建服务镜像..."
docker compose build

echo "[5/5] 导出镜像 tar..."
docker save -o offline/images/ollama.tar ollama/ollama:latest
docker save -o offline/images/analyzer.tar avs-analyzer:latest
docker save -o offline/images/executor.tar avs-executor:latest
docker save -o offline/images/planner.tar avs-planner:latest

echo "构建完成。把整个 ai-video-studio 文件夹复制到目标机，运行: ./scripts/load-and-start.sh"
