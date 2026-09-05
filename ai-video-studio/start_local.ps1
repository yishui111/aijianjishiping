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
# 字幕模型：models\whisper-medium（更精准的 medium，约1.5GB）存在则优先用，否则用内置 small
$WhisperDir = if (Test-Path (Join-Path $Root "models\whisper-medium\model.bin")) { Join-Path $Root "models\whisper-medium" } else { Join-Path $Root "models\whisper" }
$OutputDir = Join-Path $Root "output"
$ConfigDir = Join-Path $Root "config"
$LogDir = Join-Path $Root "runtime\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# ---------- 读取 .env（可选） ----------
# .env 里的 KEY=VALUE 覆盖下方脚本默认值：如切线上 DeepSeek（PLANNER_BASE_URL/PLANNER_API_KEY/
# GEN_SCRIPT_API_KEY）、关视觉大模型（VLM_ENABLED=false，用 CLIP 场景标注兜底）。
$DotEnv = @{}
$DotEnvFile = Join-Path $Root ".env"
if (Test-Path $DotEnvFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($DotEnvFile, [System.Text.Encoding]::UTF8)) {
        $t = $line.Trim()
        if ($t -match "^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$") {
            $v = $Matches[2].Trim().Trim('"').Trim("'")
            if ($v -ne "") { $DotEnv[$Matches[1]] = $v }
        }
    }
    Write-Host ("  已加载 .env（{0} 项配置）" -f $DotEnv.Count)
}
# 有 .env 用 .env，否则用脚本默认值；默认值为空 = 不注入（保持继承）
function Set-SvcEnv([string]$Key, [string]$Default) {
    if ($DotEnv.ContainsKey($Key)) { [Environment]::SetEnvironmentVariable($Key, $DotEnv[$Key], "Process") }
    elseif ($Default -ne "") { [Environment]::SetEnvironmentVariable($Key, $Default, "Process") }
}

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
        Set-SvcEnv "VLM_BASE_URL" "http://127.0.0.1:11434/v1"
        Set-SvcEnv "VLM_MODEL" "qwen2.5vl:3b"
        Set-SvcEnv "VLM_ENABLED" "auto"   # 本机跑不动视觉大模型时在 .env 设 false
        Set-SvcEnv "NUM_CTX" "8192"
        Set-SvcEnv "ASR_MODEL" "faster-whisper-small"
        Set-SvcEnv "ASR_DEVICE" "auto"   # 本机缺 CUDA 运行库时在 .env 设 cpu
        Set-SvcEnv "WHISPER_MODEL_DIR" $WhisperDir
        Set-SvcEnv "ASR_CPU_THREADS" "4"
        Set-SvcEnv "FACE_ENABLED" "false"
        Set-SvcEnv "CHINESE_CLIP_DIR" (Join-Path $Root "models\chinese-clip")
    } elseif ($port -eq 8002) {
        Set-SvcEnv "OUTPUT_DIR" $OutputDir
    } else {
        # 对话/剧本模型：.env 配了线上 API（PLANNER_BASE_URL / GEN_SCRIPT_API_KEY）就走线上，否则本地 Ollama
        Set-SvcEnv "PLANNER_BASE_URL" "http://127.0.0.1:11434/v1"
        Set-SvcEnv "PLANNER_MODEL" "qwen2.5:7b"
        Set-SvcEnv "PLANNER_API_KEY" ""
        Set-SvcEnv "GEN_SCRIPT_BASE_URL" "https://api.deepseek.com/v1"
        Set-SvcEnv "GEN_SCRIPT_MODEL" "deepseek-chat"
        Set-SvcEnv "GEN_SCRIPT_API_KEY" ""
        Set-SvcEnv "OLLAMA_BASE" "http://127.0.0.1:11434"   # bge-m3 向量化/模型状态固定走本地 Ollama
        Set-SvcEnv "VLM_MODEL" "qwen2.5vl:3b"
        Set-SvcEnv "CHINESE_CLIP_DIR" (Join-Path $Root "models\chinese-clip")
        Set-SvcEnv "ANALYZER_URL" "http://127.0.0.1:8001"
        Set-SvcEnv "EXECUTOR_URL" "http://127.0.0.1:8002"
        Set-SvcEnv "OUTPUT_DIR" $OutputDir
    }
    Start-Process -FilePath $VenvPython -ArgumentList "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$port" `
        -WorkingDirectory $svcDir -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err"
}
Start-Sleep -Seconds 3

# ---------- 3. 检查状态（轮询等待，analyzer 导入 torch 可能要 30~60 秒） ----------
Write-Host "[3/3] 检查服务状态 ..."
$pending = @(8001, 8002, 8003)
$deadline = (Get-Date).AddSeconds(90)
while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline) {
    foreach ($port in @($pending)) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$port/health" -UseBasicParsing -TimeoutSec 3
            Write-Host "  [OK] :$port HTTP $($r.StatusCode)"
            $pending = @($pending | Where-Object { $_ -ne $port })
        } catch { }
    }
    if ($pending.Count -gt 0) { Start-Sleep -Seconds 3 }
}
foreach ($port in $pending) {
    Write-Host "  警告 :$port 等待 90 秒仍未就绪（首次启动导入模型库可能较慢，可稍后访问 http://localhost:$port/health 验证）"
}

Write-Host ""
Write-Host "===== 启动完成 ====="
Write-Host "  网页界面 : http://localhost:8003"
Write-Host "  理解服务 : http://127.0.0.1:8001"
Write-Host "  执行服务 : http://127.0.0.1:8002"
Write-Host "  模型按需加载：启动不加载任何模型；分析有免 VLM 兜底（CLIP 标注），.env 设 VLM_ENABLED=false 可彻底关闭视觉大模型"
Write-Host "  关闭     : .\stop_local.ps1"