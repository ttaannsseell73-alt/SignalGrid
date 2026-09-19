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

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    foreach ($raw in Get-Content $Path) {
        $line = $raw.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) { continue }
        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($name) { Set-Item -Path ("Env:" + $name) -Value $value }
    }
}

$envPath = Join-Path $repoRoot ".env"
$envExamplePath = Join-Path $repoRoot ".env.example"
if (-not (Test-Path $envPath)) {
    Copy-Item $envExamplePath $envPath -Force
}

Import-DotEnv $envPath

if ([string]::IsNullOrWhiteSpace($env:BINANCE_DEMO_API_KEY) -or
    [string]::IsNullOrWhiteSpace($env:BINANCE_DEMO_API_SECRET)) {
    Write-Host ""
    Write-Host "[SignalGrid Scalping] Ilk kurulum: Demo API bilgilerini .env dosyasina girin."
    Write-Host "[SignalGrid Scalping] Dosya: $envPath"
    Start-Process notepad.exe $envPath
    Write-Host "[SignalGrid Scalping] Kaydedip kapatin; sonra RUN_SCALPING_DEMO.cmd dosyasina tekrar cift tiklayin."
    exit 2
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

Write-Host "[SignalGrid Scalping] Paket kurulumu/guncellemesi..."
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "SignalGrid kurulumu basarisiz." }

$symbols = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_SYMBOLS)) {
    "BTCUSDT"
} else {
    $env:SIGNALGRID_SCALPING_SYMBOLS.Trim()
}
$hours = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_HOURS)) { "1" } else { $env:SIGNALGRID_SCALPING_HOURS.Trim() }
$sampleSeconds = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_SCALPING_SAMPLE_SECONDS)) { "15" } else { $env:SIGNALGRID_SCALPING_SAMPLE_SECONDS.Trim() }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $repoRoot ("runs\scalping-" + $timestamp)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$dbPath = Join-Path $runDir "signalgrid-scalping.db"
$journalPath = Join-Path $runDir "scalping-demo.jsonl"
$logPath = Join-Path $runDir "scalping-console.log"

Write-Host ""
Write-Host "=== SIGNALGRID SCALPING V1 / BINANCE FUTURES DEMO ==="
Write-Host "Symbols: $symbols"
Write-Host "Leverage: 3x"
Write-Host "Run dir: $runDir"
Write-Host ""

$env:PYTHONUNBUFFERED = "1"
& $venvPython -m signalgrid.ops.run_scalping_demo `
    --symbols $symbols `
    --hours $hours `
    --sample-seconds $sampleSeconds `
    --db $dbPath `
    --journal $journalPath `
    --overwrite-journal `
    --pretty 2>&1 | Tee-Object -FilePath $logPath

$exitCode = $LASTEXITCODE
Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[SignalGrid Scalping] DEMO: PASS"
} else {
    Write-Host "[SignalGrid Scalping] DEMO: FAIL (exit=$exitCode)"
}
Write-Host "Log: $logPath"
exit $exitCode
