# 目标机脚本：离线加载镜像并启动（全程不需要联网）。
param([switch]$Gpu)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (Test-Path "offline/images") {
    Get-ChildItem "offline/images" -Filter *.tar | ForEach-Object {
        Write-Host "加载镜像: $($_.Name)"
        docker load -i $_.FullName
    }
}

if ($Gpu) {
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
} else {
    docker compose up -d
}
Start-Sleep -Seconds 8
docker compose ps
Write-Host ""
Write-Host "对话界面:  http://localhost:8003"
Write-Host "理解接口:  http://localhost:8001  (POST /scan 批量分析)"
