# 功能：重啟 AI Whisper Next 並將 stdout/stderr 寫入時間戳 log 檔
# 職責：終止舊程序、啟動 run_ai_whisper.py、產生 ai_whisper_yyyyMMdd_HHmmss.log

param(
    [switch]$NoKill  # 若有 -NoKill 則不先終止 python/AI Whisper
)

$ErrorActionPreference = 'SilentlyContinue'
$workspace = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

Set-Location $workspace

if (-not $NoKill) {
    taskkill /F /IM "AI Whisper.exe" 2>$null | Out-Null
    taskkill /F /IM python.exe 2>$null | Out-Null
    Start-Sleep -Seconds 2
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Write-Host "[$ts] Restart AI Whisper Next"
$env:PYTHONUNBUFFERED = "1"
Start-Process cmd -ArgumentList "/c", "`"$workspace\.venv\Scripts\python.exe`" -u run_ai_whisper.py" -WorkingDirectory $workspace -WindowStyle Hidden

Start-Sleep -Seconds 4
# 找 run_ai_whisper.py 自己建立的最新 log 檔
$logFull = Get-ChildItem -Path $workspace -Filter "ai_whisper_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if ($logFull) {
    Write-Host "--- LOG (last 15 lines) ---"
    Get-Content $logFull -Tail 15 -Encoding UTF8
}
Write-Host "`nLOG: $logFull"
