# 构建机脚本：把依赖、模型、镜像全部缓存到本文件夹，供目标机离线部署。
# 用法: .\scripts\build-offline.ps1 -VlmModel qwen2.5vl:3b [-Include7B] [-SkipWhisper]
param(
    [string]$VlmModel = "qwen2.5vl:3b",
    [switch]$Include7B,
    [switch]$SkipWhisper
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$root = (Get-Location).Path

foreach ($d in @("offline/wheels", "offline/images", "models/ollama", "models/whisper")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

Write-Host "[1/5] 缓存 pip 依赖（Linux wheel，供容器离线安装）..."
docker pull python:3.12-slim
$pipCmd = "pip download -r /services/analyzer/requirements.txt -d /wheels && " +
          "pip download -r /services/executor/requirements.txt -d /wheels && " +
          "pip download -r /services/planner/requirements.txt -d /wheels"
docker run --rm -v "${root}/offline/wheels:/wheels" -v "${root}/services:/services:ro" python:3.12-slim sh -c $pipCmd

Write-Host "[2/5] 拉取 Ollama 镜像并预下载模型..."
docker pull ollama/ollama
docker rm -f avs-build-ollama 2>$null
docker run -d --name avs-build-ollama -v "${root}/models/ollama:/root/.ollama" ollama/ollama
$models = @($VlmModel)
if ($Include7B) { $models += "qwen2.5vl:7b" }
foreach ($m in $models) {
    Write-Host "  ollama pull $m"
    docker exec avs-build-ollama ollama pull $m
}
docker stop avs-build-ollama
docker rm avs-build-ollama

if (-not $SkipWhisper) {
    Write-Host "[3/5] 缓存 faster-whisper small 模型（离线转写用）..."
    $pythonCmd = 'from faster_whisper import download_model; download_model("small", output_dir="/models/whisper")'
    docker run --rm -v "${root}/offline/wheels:/wheels" -v "${root}/models/whisper:/models/whisper" python:3.12-slim sh -c "pip install --no-index --find-links=/wheels faster-whisper && python -c `"$pythonCmd`""
} else {
    Write-Host "[3/5] 跳过 whisper 模型（目标机将无转写能力）"
}

Write-Host "[4/5] 构建服务镜像..."
docker compose build

Write-Host "[5/5] 导出镜像 tar..."
docker save -o offline/images/ollama.tar ollama/ollama:latest
docker save -o offline/images/analyzer.tar avs-analyzer:latest
docker save -o offline/images/executor.tar avs-executor:latest
docker save -o offline/images/planner.tar avs-planner:latest

Write-Host ""
Write-Host "构建完成。把整个 ai-video-studio 文件夹复制到目标机，运行:"
Write-Host "  .\scripts\load-and-start.ps1"
