# ============================================================
# AI Video Studio 启动脚本 (Windows PowerShell)
#
# 功能：
#   1. 先停止 Docker 中其他项目的所有容器，释放主机资源
#   2. 再启动本项目的 avs-* 四个服务 (ollama/analyzer/executor/planner)
#
# 用法：
#   右键 -> 使用 PowerShell 运行
#   或命令行: powershell -ExecutionPolicy Bypass -File .\start.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$ProjectPrefix = "avs-"   # 本项目所有容器统一前缀

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
Write-Host "===== AI Video Studio 启动 ====="

# ---------- 1. 停止其他项目的容器 (释放资源) ----------
Write-Host ""
Write-Host "[1/3] 检查并停止其他项目的容器 ..."
$all = @(docker ps --format "{{.Names}}")
$others = @($all | Where-Object { $_ -and $_ -notlike "$ProjectPrefix*" })
if ($others.Count -gt 0) {
    Write-Host "  将停止以下其他项目容器:"
    $others | ForEach-Object { Write-Host "    - $_" }
    # 先拼好容器名列表再调用（字符串内不能直接写数组表达式）
    $names = $others -join ' '
    $null = Invoke-Docker "docker stop $names"
    Write-Host "  已停止 $($others.Count) 个其他容器"
} else {
    Write-Host "  没有发现其他项目的容器在运行"
}

# ---------- 2. 启动本项目 ----------
Write-Host ""
Write-Host "[2/3] 启动 AI Video Studio 服务 ..."
$code = Invoke-Docker "docker compose up -d"
if ($code -ne 0) { throw "docker compose up 失败 (exit=$code)" }
Write-Host "  服务已启动"

# ---------- 3. 等待并检查状态 ----------
Write-Host ""
Write-Host "[3/3] 等待服务就绪 ..."
Start-Sleep -Seconds 8
$null = Invoke-Docker "docker compose ps"

Write-Host ""
Write-Host "===== 启动完成 ====="
Write-Host "  对话界面 : http://localhost:8003"
Write-Host "  理解服务 : http://localhost:8001"
Write-Host "  执行服务 : http://localhost:8002"
Write-Host ""
Write-Host "关闭本项目: .\stop.ps1"
