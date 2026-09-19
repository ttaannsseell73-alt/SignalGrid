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
    if ($launcher.Count -eq 2) {
        & $launcher[0] $launcher[1] -m venv $venvDir
    } else {
        & $launcher[0] -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "venv olusturulamadi." }
}

& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "SignalGrid kurulumu basarisiz." }

$symbol = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_VALIDATE_SYMBOL)) {
    "BTCUSDT"
} else {
    $env:SIGNALGRID_SCALPING_VALIDATE_SYMBOL.Trim().ToUpper()
}
$days = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_DOWNLOAD_DAYS)) {
    "7"
} else {
    $env:SIGNALGRID_SCALPING_DOWNLOAD_DAYS.Trim()
}

Write-Host ""
Write-Host "=== SIGNALGRID SCALPING HISTORICAL DATA ==="
Write-Host "Symbol: $symbol"
Write-Host "Days: $days"
Write-Host "BookTicker arsivleri buyuk olabilir; indirme suresi baglantiya baglidir."
Write-Host ""

& $venvPython -m signalgrid.ops.download_scalping_data `
    --symbol $symbol `
    --days $days `
    --output-dir (Join-Path $repoRoot "data\scalping")
exit $LASTEXITCODE
