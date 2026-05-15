# 功能：重啟 AI Whisper Next 並將 stdout/stderr 寫入時間戳 log 檔
param([switch]$NoKill)

$ErrorActionPreference = 'SilentlyContinue'
$workspace = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
Set-Location $workspace

if (-not $NoKill) {
    # 先抓住現有程序（送 QUIT 前取得，才能 WaitForExit）
    $procExe = Get-Process -Name "AI Whisper" -ErrorAction SilentlyContinue
    $procPy  = Get-Process -Name "python"     -ErrorAction SilentlyContinue

    # 送 QUIT socket → quit_app() → tray.hide() → QApplication.quit()
    $tcp = New-Object System.Net.Sockets.TcpClient
    try { $tcp.Connect("127.0.0.1", 47642) } catch { }
    $graceful = $false
    if ($tcp.Connected) {
        try {
            $s = $tcp.GetStream()
            $b = [System.Text.Encoding]::ASCII.GetBytes("QUIT")
            $s.Write($b, 0, $b.Length)
            $s.Close()
        } catch { }
        $tcp.Close()

        # 等程式自己優雅退出（最多 3 秒）
        $target = if ($procExe) { $procExe } else { $procPy }
        if ($target) {
            $graceful = $target.WaitForExit(3000)
        }
    }

    if (-not $graceful) {
        # 優雅退出失敗 → 強制砍
        taskkill /F /IM "AI Whisper.exe" 2>$null | Out-Null
        taskkill /F /IM python.exe       2>$null | Out-Null
        Start-Sleep -Milliseconds 500
    }
}

$venvOffice = Join-Path $workspace ".venv-pack_office\Lib\site-packages"
$venvHome   = Join-Path $workspace ".venv-pack\Lib\site-packages"
if (Test-Path $venvOffice) { $env:PYTHONPATH = $venvOffice }
elseif (Test-Path $venvHome) { $env:PYTHONPATH = $venvHome }

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Write-Host "[$ts] Restart AI Whisper Next"
$env:PYTHONUNBUFFERED = "1"
Start-Process cmd -ArgumentList "/c", "py -3.12 -u run_ai_whisper.py" -WorkingDirectory $workspace -WindowStyle Hidden

Start-Sleep -Seconds 6
$logFull = Get-ChildItem -Path $workspace -Filter "ai_whisper_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if ($logFull) {
    Write-Host "--- LOG (last 15 lines) ---"
    Get-Content $logFull -Tail 15 -Encoding UTF8
}
Write-Host "`nLOG: $logFull"
