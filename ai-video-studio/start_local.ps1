# ============================================================
# AI Video Studio 启动脚本（无 Docker 版 / 本机原生）
# 功能：启动 Ollama（本机） + analyzer(8001) + executor(8002) + planner(8003)
# 用法：双击 一键启动.bat 或 start_local.bat
# ============================================================
$ErrorActionPreference = "Stop"
# 本机代理软件（如 Clash）会劫持 httpx 内部通信，设置 NO_PROXY 走直连
$env:NO_PROXY = "localhost,127.0.0.1,::1"
$env:no_proxy = "localhost,127.0.0.1,::1"
$Root = $PSScriptRoot
$OllamaExe = Join-Path $Root "runtime\ollama\ollama.exe"
$VenvPython = Join-Path $Root "runtime\venv\Scripts\python.exe"
$Materials = (Resolve-Path (Join-Path $Root "..\素材") -ErrorAction SilentlyContinue).Path
if (-not $Materials) { $Materials = Join-Path $Root "materials" }
$ModelsDir = Join-Path $Root "models\ollama\models"
$WhisperDir = Join-Path $Root "models\whisper"
$OutputDir = Join-Path $Root "output"
$ConfigDir = Join-Path $Root "config"
$LogDir = Join-Path $Root "runtime\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("localhost", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1000)
        if ($ok) { $c.EndConnect($iar) }
        $c.Close()
        return $ok
    } catch { return $false }
}

Write-Host ""
Write-Host "===== AI Video Studio 启动（无 Docker 版） ====="

# ---------- 1. 启动 Ollama ----------
# 说明：只启动服务，不加载任何模型（Ollama 懒加载）。
#   分析模型 qwen2.5vl:3b 只在点"分析"时才加载；2 分钟不用自动卸载释放显存。
$ollamaUp = $false
if (Test-Port 11434) {
    try { $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3; $ollamaUp = $true } catch {}
}
if (-not $ollamaUp) {
    Write-Host "[1/3] 启动 Ollama（模型目录: $ModelsDir）..."
    if (-not (Test-Path $OllamaExe)) { Write-Host "错误：未找到 Ollama: $OllamaExe"; exit 1 }
    $env:OLLAMA_MODELS = $ModelsDir
    $env:OLLAMA_KEEP_ALIVE = "2m"
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Port 11434) {
            try { $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2; $ready = $true; break } catch {}
        }
    }
    if (-not $ready) { Write-Host "错误：Ollama 启动失败（60 秒超时）"; exit 1 }
    Write-Host "  [OK] Ollama 已就绪"
} else {
    Write-Host "[1/3] Ollama 已在运行"
}

# ---------- 2. 启动三个服务 ----------
Write-Host "[2/3] 启动 analyzer(8001) / executor(8002) / planner(8003) ..."
$ts = Get-Date -Format "HHmmss"
$ports = @{ 8001 = "services\analyzer"; 8002 = "services\executor"; 8003 = "services\planner" }
foreach ($port in 8001, 8002, 8003) {
    if (Test-Port $port) { Write-Host "  警告：端口 $port 已被占用，跳过"; continue }
    $svcDir = Join-Path $Root $ports[$port]
    $logFile = Join-Path $LogDir ("log_{0}_{1}.txt" -f $port, $ts)   # 唯一文件名，避免文件占用卡住
    $env:MATERIALS_DIR = $Materials
    $env:CONFIG_DIR = $ConfigDir
    if ($port -eq 8001) {
        $env:VLM_BASE_URL = "http://127.0.0.1:11434/v1"
        $env:VLM_MODEL = "qwen2.5vl:3b"
        $env:NUM_CTX = "8192"
        $env:ASR_MODEL = "faster-whisper-small"
        $env:WHISPER_MODEL_DIR = $WhisperDir
        $env:ASR_CPU_THREADS = "4"
        $env:FACE_ENABLED = "false"
        $env:CHINESE_CLIP_DIR = Join-Path $Root "models\chinese-clip"
    } elseif ($port -eq 8002) {
        $env:OUTPUT_DIR = $OutputDir
    } else {
        $env:PLANNER_BASE_URL = "http://127.0.0.1:11434/v1"
        $env:PLANNER_MODEL = "qwen2.5:7b"
        $env:VLM_MODEL = "qwen2.5vl:3b"
        $env:CHINESE_CLIP_DIR = Join-Path $Root "models\chinese-clip"
        $env:ANALYZER_URL = "http://127.0.0.1:8001"
        $env:EXECUTOR_URL = "http://127.0.0.1:8002"
    }
    Start-Process -FilePath $VenvPython -ArgumentList "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$port" `
        -WorkingDirectory $svcDir -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err"
}
Start-Sleep -Seconds 6

# ---------- 3. 检查状态 ----------
Write-Host "[3/3] 检查服务状态 ..."
foreach ($port in 8001, 8002, 8003) {
    try { $r = Invoke-WebRequest -Uri "http://localhost:$port/health" -UseBasicParsing -TimeoutSec 5; Write-Host "  [OK] :$port HTTP $($r.StatusCode)" } catch { Write-Host "  警告 :$port 未就绪（可能还在启动）" }
}

Write-Host ""
Write-Host "===== 启动完成 ====="
Write-Host "  网页界面 : http://localhost:8003"
Write-Host "  理解服务 : http://127.0.0.1:8001"
Write-Host "  执行服务 : http://127.0.0.1:8002"
Write-Host "  模型按需加载：启动不加载任何模型，点「分析」才加载 qwen2.5vl:3b，页面会提示加载中"
Write-Host "  关闭     : .\stop_local.ps1"