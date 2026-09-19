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
    Write-Host "[SignalGrid Scalping] Sanal ortam olusturuluyor..."
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

$dataDir = Join-Path $repoRoot "data\scalping"
$klines = Join-Path $dataDir ($symbol + "-1m.csv")
$book = Join-Path $dataDir ($symbol + "-bookTicker.csv")
$funding = Join-Path $dataDir ($symbol + "-fundingRate.csv")
$gate = Join-Path $repoRoot "validation\scalping_gate.json"

if (-not (Test-Path $klines)) {
    throw "Eksik veri: $klines"
}
if (-not (Test-Path $book)) {
    throw "Eksik veri: $book"
}

$argsList = @(
    "-m", "signalgrid.backtest.scalping_cli",
    "--symbol", $symbol,
    "--klines", $klines,
    "--bookticker", $book,
    "--notional", "100",
    "--gate-output", $gate
)
if (Test-Path $funding) {
    $argsList += @("--funding", $funding)
}

Write-Host ""
Write-Host "=== SIGNALGRID SCALPING V1 QUANT VALIDATION ==="
Write-Host "Symbol: $symbol"
Write-Host "Klines: $klines"
Write-Host "BookTicker: $book"
Write-Host ""

& $venvPython @argsList
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[SignalGrid Scalping] QUANT GATE: PASS"
    Write-Host "[SignalGrid Scalping] Demo kilidi acildi: $gate"
} else {
    Write-Host "[SignalGrid Scalping] QUANT GATE: FAIL (exit=$exitCode)"
    Remove-Item $gate -ErrorAction SilentlyContinue
}
exit $exitCode
