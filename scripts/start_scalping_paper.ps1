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

$symbols = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_SYMBOLS)) { "BTCUSDT" } else { $env:SIGNALGRID_SCALPING_SYMBOLS.Trim().ToUpper() }
$hours = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_PAPER_HOURS)) { "1" } else { $env:SIGNALGRID_SCALPING_PAPER_HOURS.Trim() }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $repoRoot ("runs\scalping-paper-" + $timestamp)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$db = Join-Path $runDir "scalping-paper.db"
$journal = Join-Path $runDir "scalping-paper.jsonl"
$log = Join-Path $runDir "scalping-paper.log"

Write-Host ""
Write-Host "=== SIGNALGRID SCALPING V1 / CANONICAL PAPER ==="
Write-Host "Coin Sonar V2: ENABLED"
Write-Host "Orders: PAPER ONLY"
Write-Host "Symbols: $symbols"
Write-Host "Hours: $hours"
Write-Host "Run dir: $runDir"
Write-Host ""

$env:PYTHONUNBUFFERED = "1"
& $venvPython -m signalgrid.ops.run_scalping_paper --symbols $symbols --hours $hours --sample-seconds 15 --db $db --journal $journal --overwrite-journal --pretty 2>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
