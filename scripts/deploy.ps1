param(
    [string]$Role = "main"
)

$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $workspace ".venv-pack\Scripts\python.exe"
$distDir = Join-Path $workspace "dist\AI Whisper"
$stagedDistRoot = Join-Path $workspace "dist_build"
$stagedDistDir = Join-Path $stagedDistRoot "AI Whisper"
$configBak = Join-Path $workspace "config.json.pack.bak"

function Stop-AiWhisper {
    # Kill EXE version
    if (Get-Process -Name "AI Whisper" -ErrorAction SilentlyContinue) {
        taskkill /F /T /IM "AI Whisper.exe" 2>$null
    }
    # Kill Python dev version (run_ai_whisper.py)
    $pyProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*run_ai_whisper*' }
    foreach ($p in $pyProcs) {
        taskkill /F /T /PID $p.ProcessId 2>&1 | Out-Null
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

switch ($Role) {
    "main" {
        & powershell -ExecutionPolicy Bypass -File "$PSCommandPath" -Role build
        Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Role zip"
    }

    "build" {
        if (-not (Test-Path $python)) {
            py -m venv (Join-Path $workspace ".venv-pack")
            & $python -m pip install --upgrade pip
            & $python -m pip install -e "$workspace[dev]"
        }
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
