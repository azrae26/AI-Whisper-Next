# 功能：建置 exe（Role build）、壓縮 zip（Role zip）、或兩段連跑（Role main）。
# 職責：確保打包用 venv `.venv-pack_office` 在本機可執行（跨電腦同步時自動重建）、PyInstaller、dist 產物與備份 config。

param(
    [string]$Role = "main"
)

$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packVenvDirName = ".venv-pack_office"
$packVenvRoot = Join-Path $workspace $packVenvDirName
$python = Join-Path $packVenvRoot "Scripts\python.exe"
$distDir = Join-Path $workspace "dist\AI Whisper"
$stagedDistRoot = Join-Path $workspace "dist_build"
$stagedDistDir = Join-Path $stagedDistRoot "AI Whisper"
$configBak = Join-Path $workspace "config.json.pack.bak"

function Stop-AiWhisper {
    # Kill EXE version
    if (Get-Process -Name "AI Whisper" -ErrorAction SilentlyContinue) {
        try { taskkill /F /T /IM "AI Whisper.exe" 2>$null } catch {}
    }
    # Kill Python dev version (run_ai_whisper.py)
    $pyProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*run_ai_whisper*' }
    foreach ($p in $pyProcs) {
        try { taskkill /F /T /PID $p.ProcessId 2>$null } catch {}
    }
    for ($i = 0; $i -lt 40; $i++) {
        $exeAlive = Get-Process -Name "AI Whisper" -ErrorAction SilentlyContinue
        $pyAlive  = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
            Where-Object { $_.CommandLine -like '*run_ai_whisper*' }
        if (-not $exeAlive -and -not $pyAlive) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "AI Whisper did not exit in time"
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
        Remove-DirectoryWithRetry $distDir
        New-Item -ItemType Directory -Path (Join-Path $workspace "dist") -Force | Out-Null
        Copy-DirectoryWithRobocopy $stagedDistDir $distDir
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
            if ($tar) {
                if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
                & $tar.Source -a -c -f $zipPath -C "$workspace\dist" "AI Whisper"
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipPath)) {
                    Write-Host "tar zip failed; falling back to Compress-Archive"
                    $stagingParent = Join-Path $env:TEMP "AI_Whisper_Next_zipstaging"
                    $stagingDir = Join-Path $stagingParent "AI Whisper"
                    if (Test-Path $stagingParent) { Remove-Item $stagingParent -Recurse -Force }
                    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
                    Copy-Item "$distDir\*" $stagingDir -Recurse -Force
                    Compress-Archive -Path $stagingDir -DestinationPath $zipPath -Force
                    Remove-Item $stagingParent -Recurse -Force -ErrorAction SilentlyContinue
                }
            } else {
                $stagingParent = Join-Path $env:TEMP "AI_Whisper_Next_zipstaging"
                $stagingDir = Join-Path $stagingParent "AI Whisper"
                if (Test-Path $stagingParent) { Remove-Item $stagingParent -Recurse -Force }
                New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
                Copy-Item "$distDir\*" $stagingDir -Recurse -Force
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
