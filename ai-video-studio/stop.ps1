# ============================================================
# AI Video Studio 关闭脚本 (Windows PowerShell)
#
# 功能：
#   关闭本项目的 avs-* 四个服务容器，释放主机资源。
#   只停止并删除容器，不会删除镜像、模型或数据。
#
# 用法：
#   右键 -> 使用 PowerShell 运行
#   或命令行: powershell -ExecutionPolicy Bypass -File .\stop.ps1
# ============================================================
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# 辅助函数：执行 docker 命令，把 stderr 重定向到临时文件并丢弃，
# 避免 PS 5.1 把 docker 的进度输出显示成红色伪错误。
function Invoke-Docker {
    param([string]$CmdLine)
    $errFile = Join-Path $env:TEMP ("docker_err_" + [guid]::NewGuid().ToString("N") + ".txt")
    cmd /c "$CmdLine 2>`"$errFile`""
    $code = $LASTEXITCODE
    Remove-Item $errFile -Force -ErrorAction SilentlyContinue
    return $code
}

Write-Host ""
Write-Host "===== 关闭 AI Video Studio ====="

Write-Host "停止本项目容器 ..."
$code = Invoke-Docker "docker compose down"
if ($code -ne 0) { throw "docker compose down 失败 (exit=$code)" }

Write-Host ""
Write-Host "===== 已关闭 ====="
Write-Host "本项目所有容器已停止，主机资源已释放。"
Write-Host "重新启动: .\start.ps1"
