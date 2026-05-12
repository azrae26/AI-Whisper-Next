# 功能：純打包（不推 git）
# 用法：
#   build 完後背景壓 zip：powershell -ExecutionPolicy Bypass -File "scripts/pack.ps1"
#   build 完後等待 zip：  powershell -ExecutionPolicy Bypass -File "scripts/pack.ps1" -WaitZip
#   只更新 exe：          powershell -ExecutionPolicy Bypass -File "scripts/pack.ps1" -BuildOnly

param(
    [switch]$BuildOnly,
    [switch]$WaitZip
)

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "deploy.ps1"

& powershell -ExecutionPolicy Bypass -File $script -Role build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $BuildOnly) {
    if ($WaitZip) {
        & powershell -ExecutionPolicy Bypass -File $script -Role zip
    } else {
        Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Role zip"
        Write-Host "build complete; zip is running in background"
    }
}
