# 功能：建置 exe（Role build）、壓縮 zip（Role zip）、或兩段連跑（Role main）。
# 職責：依電腦選擇打包用 venv，確保 venv 在本機可執行（跨電腦同步時自動重建）、PyInstaller、dist 產物與備份 config；分享用 zip 不包含 exe 執行 logs。
# 備註：替換 dist\AI Whisper 前將 exe 側既有 logs（與專案根的 logs 分開）複製到 .pack_dist_exe_logs_stash，建置完成後鏡射回 dist\AI Whisper\logs，避免清空 dist 時遺失紀錄。

param(
    [string]$Role = "main"
)

$ErrorActionPreference = "Stop"

function Get-PackVenvDirName {
    $computerName = $env:COMPUTERNAME
    if ($computerName -eq "P8-32") {
        return ".venv-pack"
    }
    return ".venv-pack_office"
}

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packVenvDirName = Get-PackVenvDirName
$packVenvRoot = Join-Path $workspace $packVenvDirName
$python = Join-Path $packVenvRoot "Scripts\python.exe"
$distDir = Join-Path $workspace "dist\AI Whisper"
$stagedDistRoot = Join-Path $workspace "dist_build"
$stagedDistDir = Join-Path $stagedDistRoot "AI Whisper"
$configBak = Join-Path $workspace "config.json.pack.bak"
# 打包時暫存 dist exe 底下的 logs（非專案 logs/——避免與原始碼執行紀錄混用）
$packDistExeLogsStashDir = Join-Path $workspace ".pack_dist_exe_logs_stash"

function Stop-AiWhisper {
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
            $s.Write($b, 0, $b.Length); $s.Close()
        } catch { }
        $tcp.Close()
        $target = if ($procExe) { $procExe } else { $procPy }
        if ($target) { $graceful = $target.WaitForExit(3000) }
    }

    if (-not $graceful) {
        # 優雅退出失敗 → 強制砍
        if ($procExe) { try { taskkill /F /T /IM "AI Whisper.exe" 2>$null } catch {} }
        $pyProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
            Where-Object { $_.CommandLine -like '*run_ai_whisper*' }
        foreach ($p in $pyProcs) { try { taskkill /F /T /PID $p.ProcessId 2>$null } catch {} }
        for ($i = 0; $i -lt 40; $i++) {
            $exeAlive = Get-Process -Name "AI Whisper" -ErrorAction SilentlyContinue
            $pyAlive  = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
                Where-Object { $_.CommandLine -like '*run_ai_whisper*' }
            if (-not $exeAlive -and -not $pyAlive) { return }
            Start-Sleep -Milliseconds 250
        }
        throw "AI Whisper did not exit in time"
    }
}

function Remove-DirectoryWithRetry([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    for ($i = 0; $i -lt 40; $i++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
}

function Copy-DirectoryWithRobocopy([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    & robocopy $Source $Destination /MIR /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

function Test-PackVenvPython([string]$PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe)) { return $false }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = '-c "import sys"'
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    try {
        if (-not $p.Start()) { return $false }
        if (-not $p.WaitForExit(20000)) {
            try { $p.Kill() } catch {}
            return $false
        }
        return ($p.ExitCode -eq 0)
    } catch {
        return $false
    }
}

function Initialize-PackVenv {
    param(
        [Parameter(Mandatory)][string]$Workspace,
        [Parameter(Mandatory)][string]$PythonExe,
        [Parameter(Mandatory)][string]$VenvRoot
    )
    if ((Test-Path -LiteralPath $PythonExe) -and (Test-PackVenvPython $PythonExe)) { return }

    Write-Host "Pack venv is missing or incompatible with this PC; recreating: $VenvRoot"
    Remove-DirectoryWithRetry $VenvRoot

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pyLauncher) {
        throw '需要 py.exe 與本機已安裝的 Python 3.12 才能建立打包 venv（避免誤用 PATH 上其他主版本）。請安裝 3.12 並勾選 Python Launcher，然後執行 scripts\setup-dev-venv.ps1 自測：py -3.12 --version'
    }
    & py.exe -3.12 -m venv $VenvRoot

    if (-not (Test-Path -LiteralPath $PythonExe)) {
        throw "venv creation did not produce Scripts\python.exe"
    }
    if (-not (Test-PackVenvPython $PythonExe)) {
        throw "Pack venv python still fails after recreate; check this machine's Python install. Path: $VenvRoot"
    }

    $editable = '{0}[dev]' -f $Workspace
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -e $editable
}

switch ($Role) {
    "main" {
        & powershell -ExecutionPolicy Bypass -File "$PSCommandPath" -Role build
        Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Role zip"
    }

    "build" {
        Initialize-PackVenv -Workspace $workspace -PythonExe $python -VenvRoot $packVenvRoot
        if (Test-Path "$distDir\config.json") { Copy-Item "$distDir\config.json" $configBak -Force }
        Remove-DirectoryWithRetry $stagedDistRoot
        Set-Location $workspace
        & $python -m PyInstaller -y --onedir --windowed `
            --icon=assets/icon.ico --name="AI Whisper" `
            --distpath "$stagedDistRoot" `
            --workpath "$workspace\build" `
            --add-data "assets;assets" `
            --collect-submodules PySide6.QtCore `
            --collect-submodules PySide6.QtGui `
            --collect-submodules PySide6.QtWidgets `
            --exclude-module PySide6.Qt3DAnimation `
            --exclude-module PySide6.Qt3DCore `
            --exclude-module PySide6.Qt3DExtras `
            --exclude-module PySide6.Qt3DInput `
            --exclude-module PySide6.Qt3DLogic `
            --exclude-module PySide6.Qt3DRender `
            --exclude-module PySide6.QtBluetooth `
            --exclude-module PySide6.QtCharts `
            --exclude-module PySide6.QtDataVisualization `
            --exclude-module PySide6.QtGraphs `
            --exclude-module PySide6.QtHelp `
            --exclude-module PySide6.QtLocation `
            --exclude-module PySide6.QtMultimedia `
            --exclude-module PySide6.QtMultimediaWidgets `
            --exclude-module PySide6.QtNetworkAuth `
            --exclude-module PySide6.QtNfc `
            --exclude-module PySide6.QtPdf `
            --exclude-module PySide6.QtPdfWidgets `
            --exclude-module PySide6.QtQuick `
            --exclude-module PySide6.QtQuick3D `
            --exclude-module PySide6.QtQuickControls2 `
            --exclude-module PySide6.QtQuickWidgets `
            --exclude-module PySide6.QtRemoteObjects `
            --exclude-module PySide6.QtScxml `
            --exclude-module PySide6.QtSensors `
            --exclude-module PySide6.QtSerialBus `
            --exclude-module PySide6.QtSerialPort `
            --exclude-module PySide6.QtSpatialAudio `
            --exclude-module PySide6.QtSql `
            --exclude-module PySide6.QtStateMachine `
            --exclude-module PySide6.QtSvg `
            --exclude-module PySide6.QtSvgWidgets `
            --exclude-module PySide6.QtTextToSpeech `
            --exclude-module PySide6.QtUiTools `
            --exclude-module PySide6.QtWebChannel `
            --exclude-module PySide6.QtWebEngineCore `
            --exclude-module PySide6.QtWebEngineQuick `
            --exclude-module PySide6.QtWebEngineWidgets `
            --exclude-module PySide6.QtWebSockets `
            --exclude-module PySide6.QtXml `
            --hidden-import comtypes.stream `
            --version-file "$workspace\version_info.txt" `
            "$workspace\run_ai_whisper.py"
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$stagedDistDir\AI Whisper.exe")) {
            Write-Host "build failed; existing app was left running"
            exit 1
        }
        Stop-AiWhisper

        # 清空 dist 前備份 exe 側 logs\ → 根目錄 .pack_dist_exe_logs_stash（不寫入專案的 logs\）
        $distExeLogsDir = Join-Path $distDir "logs"
        $stashPackedExeLogs = $false
        if (Test-Path $distExeLogsDir) {
            $distLogFiles = @(Get-ChildItem -LiteralPath $distExeLogsDir -Recurse -File -ErrorAction SilentlyContinue)
            if ($distLogFiles.Count -gt 0) {
                Remove-DirectoryWithRetry $packDistExeLogsStashDir
                Copy-DirectoryWithRobocopy $distExeLogsDir $packDistExeLogsStashDir
                $stashPackedExeLogs = $true
                Write-Host ("build: stashed dist exe logs ({0}) -> .pack_dist_exe_logs_stash" -f $distLogFiles.Count)
            }
        }
        if (-not $stashPackedExeLogs -and (Test-Path $packDistExeLogsStashDir)) {
            Remove-DirectoryWithRetry $packDistExeLogsStashDir
        }

        Remove-DirectoryWithRetry $distDir
        New-Item -ItemType Directory -Path (Join-Path $workspace "dist") -Force | Out-Null
        Copy-DirectoryWithRobocopy $stagedDistDir $distDir
        # 將上一版 exe logs 放回 dist（與接下來自動啟動的新 .current.log 並存）
        if ($stashPackedExeLogs -and (Test-Path $packDistExeLogsStashDir)) {
            $restoredExeLogsDir = Join-Path $distDir "logs"
            New-Item -ItemType Directory -Path $restoredExeLogsDir -Force | Out-Null
            Copy-DirectoryWithRobocopy $packDistExeLogsStashDir $restoredExeLogsDir
            Remove-DirectoryWithRetry $packDistExeLogsStashDir
            Write-Host "build: restored stashed exe logs -> dist\AI Whisper\logs"
        }
        if (Test-Path $configBak) {
            Copy-Item $configBak "$distDir\config.json" -Force
            Remove-Item $configBak -Force
        }
        try {
            Remove-DirectoryWithRetry $stagedDistRoot
        } catch {
            Write-Host "warning: could not remove dist_build; it can be cleaned up later"
        }
        Start-Process -FilePath "$distDir\AI Whisper.exe" -WorkingDirectory $distDir
        Write-Host "build: dist\AI Whisper\AI Whisper.exe"
        exit 0
    }

    "zip" {
        $maxWait = 180; $elapsed = 0
        while (-not (Test-Path "$distDir\AI Whisper.exe") -and $elapsed -lt $maxWait) {
            Start-Sleep -Seconds 2; $elapsed += 2
        }
        if (Test-Path "$distDir\AI Whisper.exe") {
            $timestamp = Get-Date -Format "yyyyMMdd_HHmm"
            $zipName = "AI Whisper_$timestamp.zip"
            $zipPath = "$workspace\dist\$zipName"
            $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
            # exe logs 是本機診斷資料，只保留在 dist 執行目錄，不放進分享用 zip。
            if ($tar) {
                if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
                & $tar.Source -a -c -f $zipPath `
                    --exclude="AI Whisper/logs" `
                    --exclude="AI Whisper/logs/*" `
                    -C "$workspace\dist" "AI Whisper"
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipPath)) {
                    Write-Host "tar zip failed; falling back to Compress-Archive"
                    $stagingParent = Join-Path $env:TEMP "AI_Whisper_Next_zipstaging"
                    $stagingDir = Join-Path $stagingParent "AI Whisper"
                    if (Test-Path $stagingParent) { Remove-Item $stagingParent -Recurse -Force }
                    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
                    Get-ChildItem -LiteralPath $distDir -ErrorAction Stop |
                        Where-Object { $_.Name -ne "logs" } |
                        Copy-Item -Destination $stagingDir -Recurse -Force
                    Compress-Archive -Path $stagingDir -DestinationPath $zipPath -Force
                    Remove-Item $stagingParent -Recurse -Force -ErrorAction SilentlyContinue
                }
            } else {
                $stagingParent = Join-Path $env:TEMP "AI_Whisper_Next_zipstaging"
                $stagingDir = Join-Path $stagingParent "AI Whisper"
                if (Test-Path $stagingParent) { Remove-Item $stagingParent -Recurse -Force }
                New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
                Get-ChildItem -LiteralPath $distDir -ErrorAction Stop |
                    Where-Object { $_.Name -ne "logs" } |
                    Copy-Item -Destination $stagingDir -Recurse -Force
                Compress-Archive -Path $stagingDir -DestinationPath $zipPath -Force
                Remove-Item $stagingParent -Recurse -Force -ErrorAction SilentlyContinue
            }
            Get-ChildItem "$workspace\dist\AI Whisper_*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Force
            Write-Host "zip: dist\$zipName"
        } else {
            Write-Host "Build timed out or failed"
        }
    }
}
