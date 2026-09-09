# ============================================================
# AI Video Studio 停止脚本（无 Docker 版）
# 停止三个 uvicorn 服务（Ollama 保持运行，可手动关闭或保留供其他项目用）
# ============================================================
Write-Host "===== AI Video Studio 停止（无 Docker 版） ====="
foreach ($port in 61801, 61802, 61803) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -match 'python') {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  已停止 :$port (PID $($proc.Id))"
        }
    }
}
Write-Host "完成。Ollama 仍在运行（如需关闭：taskkill /im ollama.exe /f）"
