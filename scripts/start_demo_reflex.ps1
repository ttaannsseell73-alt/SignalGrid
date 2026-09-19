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
    if (Test-Path $envExamplePath) {
        Copy-Item $envExamplePath $envPath -Force
    } else {
        @"
BINANCE_DEMO_API_KEY=
BINANCE_DEMO_API_SECRET=
SIGNALGRID_DEMO_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
SIGNALGRID_DEMO_HOURS=1
SIGNALGRID_DEMO_SAMPLE_SECONDS=30
"@ | Set-Content -Path $envPath -Encoding UTF8
    }
}

Import-DotEnv $envPath

$apiKey = ($env:BINANCE_DEMO_API_KEY | ForEach-Object { "$_".Trim() })
$apiSecret = ($env:BINANCE_DEMO_API_SECRET | ForEach-Object { "$_".Trim() })

if ([string]::IsNullOrWhiteSpace($apiKey) -or [string]::IsNullOrWhiteSpace($apiSecret)) {
    Write-Host ""
    Write-Host "[SignalGrid] Ilk kurulum: Binance Futures Demo API bilgilerini .env dosyasina girin."
    Write-Host "[SignalGrid] Dosya: $envPath"
    Start-Process notepad.exe $envPath
    Write-Host "[SignalGrid] Kaydedip Not Defteri'ni kapatin, sonra RUN_DEMO.cmd dosyasina tekrar cift tiklayin."
    exit 2
}

$launcher = Find-PythonLauncher
$venvDir = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "[SignalGrid] Sanal ortam olusturuluyor..."
    if ($launcher.Count -eq 2) {
        & $launcher[0] $launcher[1] -m venv $venvDir
    } else {
        & $launcher[0] -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "venv olusturulamadi." }
}

Write-Host "[SignalGrid] Paket kurulumu/guncellemesi..."
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "SignalGrid kurulumu basarisiz." }

$env:BINANCE_DEMO_API_KEY = $apiKey
$env:BINANCE_DEMO_API_SECRET = $apiSecret

if (-not ("SignalGrid.Power" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
namespace SignalGrid {
    public static class Power {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint SetThreadExecutionState(uint esFlags);
    }
}
"@
}

$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
[void][SignalGrid.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)

$symbols = if ([string]::IsNullOrWhiteSpace($env:SIGNALGRID_DEMO_SYMBOLS)) {
    "BTCUSDT,ETHUSDT,SOLUSDT"
} else {
    $env:SIGNALGRID_DEMO_SYMBOLS.Trim()
}

$hours = 1.0
if (-not [string]::IsNullOrWhiteSpace($env:SIGNALGRID_DEMO_HOURS)) {
    $parsedHours = 0.0
    if ([double]::TryParse($env:SIGNALGRID_DEMO_HOURS, [ref]$parsedHours) -and $parsedHours -gt 0) {
        $hours = $parsedHours
    }
}

$sampleSeconds = 30.0
if (-not [string]::IsNullOrWhiteSpace($env:SIGNALGRID_DEMO_SAMPLE_SECONDS)) {
    $parsedSample = 0.0
    if ([double]::TryParse($env:SIGNALGRID_DEMO_SAMPLE_SECONDS, [ref]$parsedSample) -and $parsedSample -gt 0) {
        $sampleSeconds = $parsedSample
    }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $repoRoot ("runs\demo-reflex-" + $timestamp)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$dbPath = Join-Path $runDir "signalgrid-demo.db"
$journalPath = Join-Path $runDir "demo-reflex.jsonl"
$logPath = Join-Path $runDir "demo-reflex-console.log"

Write-Host ""
Write-Host "[SignalGrid] BINANCE FUTURES DEMO BOT BASLIYOR"
Write-Host "[SignalGrid] Symbols: $symbols"
Write-Host "[SignalGrid] Sure: $hours saat"
Write-Host "[SignalGrid] Leverage: 3x"
Write-Host "[SignalGrid] Run dir: $runDir"
Write-Host "[SignalGrid] Emir ve pozisyonlari Binance Demo Trading ekranindan izleyebilirsiniz."
Write-Host ""

$previousErrorActionPreference = $ErrorActionPreference
$exitCode = 1
try {
    $ErrorActionPreference = "Continue"
    $env:PYTHONUNBUFFERED = "1"
    & $venvPython -m signalgrid.ops.run_demo_reflex `
        --symbols $symbols `
        --hours $hours `
        --sample-seconds $sampleSeconds `
        --db $dbPath `
        --journal $journalPath `
        --overwrite-journal `
        --pretty 2>&1 | Tee-Object -FilePath $logPath
    $exitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
    [void][SignalGrid.Power]::SetThreadExecutionState($ES_CONTINUOUS)
    Remove-Item Env:BINANCE_DEMO_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_DEMO_API_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_API_SECRET -ErrorAction SilentlyContinue
}

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[SignalGrid] DEMO REFLEX TEST: PASS"
} else {
    Write-Host "[SignalGrid] DEMO REFLEX TEST: FAIL (exit=$exitCode)"
}
Write-Host "[SignalGrid] Journal: $journalPath"
Write-Host "[SignalGrid] Log: $logPath"
exit $exitCode
