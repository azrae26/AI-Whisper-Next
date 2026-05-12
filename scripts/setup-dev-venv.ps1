# 功能：在專案根目錄建立 / 修復 `.venv`（固定 Python 3.12），並安裝 editable + dev 依賴。
# 職責：兩台電腦 Python 預設版本不同時，仍用同一條 3.12 線與本機 venv，避免複製他機 venv 造成路徑錯亂。

$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvRoot = Join-Path $workspace ".venv"
$pyExe = Join-Path $venvRoot "Scripts\python.exe"
$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $pyLauncher) {
    throw '未找到 py.exe。請安裝 Python 3.12（https://www.python.org/downloads/）並勾選 Install launcher for all users。'
}

function Test-VenvPython312 {
    param([Parameter(Mandatory)][string]$PythonExe)
    if (-not (Test-Path -LiteralPath $PythonExe)) { return $false }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = '-c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)"'
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    if (-not $p.Start()) { return $false }
    if (-not $p.WaitForExit(20000)) {
        try { $p.Kill() } catch {}
        return $false
    }
    return ($p.ExitCode -eq 0)
}

function Remove-DirRetry([string]$Path) {
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

$needCreate = $false
if (-not (Test-Path -LiteralPath $pyExe)) {
    $needCreate = $true
} elseif (-not (Test-VenvPython312 -PythonExe $pyExe)) {
    Write-Host ".venv 存在但非 Python 3.12 或已損毀，將重建: $venvRoot"
    Remove-DirRetry $venvRoot
    $needCreate = $true
}

if ($needCreate) {
    Write-Host "建立開發用 venv（py -3.12）: $venvRoot"
    & py.exe -3.12 -m venv $venvRoot
    if (-not (Test-Path -LiteralPath $pyExe)) {
        throw 'venv 建立失敗。請確認已安裝 Python 3.12（指令: py -3.12 --version）。'
    }
    if (-not (Test-VenvPython312 -PythonExe $pyExe)) {
        throw 'venv 內 Python 不是 3.12。請檢查 py -0p 所列 3.12 路徑。'
    }
}

$editable = '{0}[dev]' -f $workspace
& $pyExe -m pip install --upgrade pip
& $pyExe -m pip install -e $editable
Write-Host "完成。啟用後執行開發模式："
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python -m ai_whisper"
