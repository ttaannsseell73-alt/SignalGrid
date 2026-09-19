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

$symbolsText = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_PORTFOLIO_SYMBOLS)) { "BTCUSDT,ETHUSDT,SOLUSDT" } else { $env:SIGNALGRID_SCALPING_PORTFOLIO_SYMBOLS.Trim().ToUpper() }
$symbols = $symbolsText.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$days = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_DOWNLOAD_DAYS)) { "30" } else { $env:SIGNALGRID_SCALPING_DOWNLOAD_DAYS.Trim() }
$dataDir = Join-Path $repoRoot "data\scalping"
$gatePath = Join-Path $repoRoot "validation\scalping_gate.json"

Write-Host ""
Write-Host "=== SIGNALGRID SCALPING MULTI-SYMBOL PIPELINE ==="
Write-Host "Symbols: $symbolsText"
Write-Host "Days: $days"

foreach ($symbol in $symbols) {
    Write-Host ""
    Write-Host "[1/2] Historical data: $symbol"
    & $venvPython -m signalgrid.ops.download_scalping_data --symbol $symbol --days $days --output-dir $dataDir
    if ($LASTEXITCODE -ne 0) { throw "Historical data download failed for $symbol" }
}

Write-Host ""
Write-Host "[2/2] Portfolio quant validation"
& $venvPython -m signalgrid.backtest.scalping_portfolio_cli --symbols $symbolsText --data-dir $dataDir --gate-output $gatePath
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[SignalGrid Scalping] MULTI-SYMBOL QUANT GATE: PASS"
    Write-Host "[SignalGrid Scalping] Validated symbols are eligible for Binance Demo."
} else {
    Write-Host "[SignalGrid Scalping] MULTI-SYMBOL QUANT GATE: FAIL"
    Write-Host "[SignalGrid Scalping] Demo gate remains locked for the failed portfolio."
}
exit $exitCode
