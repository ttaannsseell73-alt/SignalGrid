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

Write-Host ""
Write-Host "[SignalGrid] Binance FUTURES DEMO API bilgilerini girin."
Write-Host "[SignalGrid] Secret ekrana yazilmaz ve dosyaya kaydedilmez."
$apiKey = Read-Host "Demo API Key"
$secureSecret = Read-Host "Demo API Secret" -AsSecureString
if ([string]::IsNullOrWhiteSpace($apiKey)) { throw "Demo API Key bos olamaz." }

$secretPtr = [IntPtr]::Zero
$plainSecret = $null
try {
    $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
    $plainSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
    if ([string]::IsNullOrWhiteSpace($plainSecret)) { throw "Demo API Secret bos olamaz." }

    $env:BINANCE_DEMO_API_KEY = $apiKey.Trim()
    $env:BINANCE_DEMO_API_SECRET = $plainSecret

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

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runDir = Join-Path $repoRoot ("runs\demo-reflex-" + $timestamp)
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    $dbPath = Join-Path $runDir "signalgrid-demo.db"
    $journalPath = Join-Path $runDir "demo-reflex.jsonl"
    $logPath = Join-Path $runDir "demo-reflex-console.log"

    # Two controls + liquid/high-volatility stress names selected for the first Demo run.
    $symbols = "BTCUSDT,ETHUSDT,UNIUSDT,NEARUSDT,ZECUSDT,ONDOUSDT,FETUSDT,APTUSDT,ARBUSDT,AAVEUSDT,ENAUSDT"

    Write-Host ""
    Write-Host "[SignalGrid] BINANCE FUTURES DEMO reflex testi basliyor."
    Write-Host "[SignalGrid] Sure: 1 saat"
    Write-Host "[SignalGrid] Leverage: 3x (preflight ile Binance Demo'da sabitlenecek)"
    Write-Host "[SignalGrid] Symbols: $symbols"
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
            --hours 1 `
            --sample-seconds 30 `
            --db $dbPath `
            --journal $journalPath `
            --pretty 2>&1 | Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        [void][SignalGrid.Power]::SetThreadExecutionState($ES_CONTINUOUS)
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
}
finally {
    Remove-Item Env:BINANCE_DEMO_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_DEMO_API_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_API_SECRET -ErrorAction SilentlyContinue
    $plainSecret = $null
    if ($secretPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
    }
}
