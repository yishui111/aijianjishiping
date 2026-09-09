$ErrorActionPreference = "Stop"
[System.Net.WebRequest]::DefaultWebProxy = $null
$env:NO_PROXY = "localhost,127.0.0.1,::1"
$env:no_proxy = $env:NO_PROXY
# 离线运行：模型缓存全部在本地文件夹内
$env:MODELSCOPE_CACHE = (Join-Path $PSScriptRoot "modelscope-cache")
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$Root = $PSScriptRoot
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1000)
        if ($ok) { $c.EndConnect($iar) }
        $c.Close()
        return $ok
    } catch { return $false }
}

function Start-Svc([int]$Port, [string]$Name, [string[]]$ProcArgs, [string]$LogFile) {
    if (Test-Port $Port) { Write-Host "[OK] $Name 已在运行（端口 $Port）"; return }
    Write-Host "启动 $Name（端口 $Port）..."
    Start-Process -FilePath (Join-Path $Root "runtime\Scripts\python.exe") `
        -ArgumentList $ProcArgs -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir $LogFile) `
        -RedirectStandardError (Join-Path $LogDir ($LogFile + ".err"))
    $ready = $false
    for ($i = 0; $i -lt 150; $i++) {
        Start-Sleep -Seconds 2
        try {
            $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/" -TimeoutSec 3
            $ready = $true; break
        } catch {}
        if ($i % 5 -eq 4) { Write-Host ("  等待中... " + (($i + 1) * 2) + "s") }
    }
    if (-not $ready) { Write-Host "  警告：$Name 启动超时（首次会下载语音模型，日志在 logs\$LogFile.err）"; return }
    Write-Host "  [OK] $Name 就绪"
}

Start-Svc 61810 "剪辑工作台" @("funclip/launch.py", "--port", "61810") "studio.log"
Start-Svc 61812 "分析服务" @("-m", "uvicorn", "api_service:app", "--host", "127.0.0.1", "--port", "61812") "api.log"

Write-Host "正在打开浏览器..."
Start-Process "http://127.0.0.1:61810"
Start-Process "http://127.0.0.1:61812"