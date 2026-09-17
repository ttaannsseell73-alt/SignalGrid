$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Find-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @("-3.12", "-3.13", "-3.11")) {
            & py $version --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @("py", $version)
            }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @("python")
        }
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
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip guncellenemedi." }
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "SignalGrid kurulumu basarisiz." }

# Keep Windows awake only while this PowerShell process is running.
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
$ES_CONTINUOUS = [uint32]0x80000000
$ES_SYSTEM_REQUIRED = [uint32]0x00000001
[void][SignalGrid.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $repoRoot ("runs\paper-24h-" + $timestamp)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$dbPath = Join-Path $runDir "signalgrid-paper.db"
$journalPath = Join-Path $runDir "paper-24h.jsonl"
$logPath = Join-Path $runDir "paper-24h-console.log"

$symbols = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT,LTCUSDT,AVAXUSDT"

Write-Host ""
Write-Host "[SignalGrid] PAPER 24h soak basliyor."
Write-Host "[SignalGrid] Symbols: $symbols"
Write-Host "[SignalGrid] Run dir: $runDir"
Write-Host "[SignalGrid] Bu pencereyi kapatmayin. Windows uyku modu bu sure boyunca engellendi."
Write-Host ""

$exitCode = 1
try {
    & $venvPython -m signalgrid.ops.run_soak `
        --mode paper `
        --symbols $symbols `
        --hours 24 `
        --sample-seconds 60 `
        --db $dbPath `
        --journal $journalPath `
        --pretty 2>&1 | Tee-Object -FilePath $logPath
    $exitCode = $LASTEXITCODE
}
finally {
    [void][SignalGrid.Power]::SetThreadExecutionState($ES_CONTINUOUS)
}

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[SignalGrid] PAPER 24h SOAK: PASS"
} else {
    Write-Host "[SignalGrid] PAPER 24h SOAK: FAIL (exit=$exitCode)"
}
Write-Host "[SignalGrid] Journal: $journalPath"
Write-Host "[SignalGrid] Log: $logPath"
exit $exitCode
