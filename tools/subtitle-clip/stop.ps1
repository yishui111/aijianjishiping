foreach ($port in 61810, 61812) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
    if ($c) { foreach ($p in $c) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue; Write-Host ("已停止端口 " + $port + " (PID " + $p + ")") } }
}
Write-Host "完成"