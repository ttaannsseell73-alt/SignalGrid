$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Find-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @("-3.12", "-3.13", "-3.11")) {
            & py $version --version *> $null
            if ($LASTEXITCODE -eq 0) { return @("py", $version) }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python --version *> $null
        if ($LASTEXITCODE -eq 0) { return @("python") }
    }
    throw "Python 3.11+ bulunamadi."
}

$launcher = Find-PythonLauncher
$venvDir = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    if ($launcher.Count -eq 2) { & $launcher[0] $launcher[1] -m venv $venvDir }
    else { & $launcher[0] -m venv $venvDir }
    if ($LASTEXITCODE -ne 0) { throw "venv olusturulamadi." }
}

& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "SignalGrid kurulumu basarisiz." }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $repoRoot ("runs\shadow-" + $timestamp)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$db = Join-Path $runDir "scalping-shadow.db"

$symbols = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SHADOW_SYMBOLS)) {
    "BTCUSDT,ETHUSDT,SOLUSDT"
} else { $env:SIGNALGRID_SHADOW_SYMBOLS.Trim().ToUpper() }
$minutes = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SHADOW_MINUTES)) {
    "60"
} else { $env:SIGNALGRID_SHADOW_MINUTES.Trim() }

Write-Host ""
Write-Host "=== SIGNALGRID SHADOW MICROSTRUCTURE RECORDER ==="
Write-Host "Symbols: $symbols"
Write-Host "Minutes: $minutes"
Write-Host "Orders: DISABLED"
Write-Host "API keys: NOT REQUIRED"
Write-Host "DB: $db"
Write-Host ""

& $venvPython -m signalgrid.ops.run_scalping_shadow --symbols $symbols --minutes $minutes --db $db
exit $LASTEXITCODE
