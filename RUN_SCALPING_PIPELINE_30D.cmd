@echo off
setlocal
cd /d "%~dp0"
set "SIGNALGRID_SCALPING_DOWNLOAD_DAYS=30"
echo === 1/2 HISTORICAL DATA ===
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\download_scalping_data.ps1"
if errorlevel 1 (
  echo.
  echo Historical data step failed.
  pause
  exit /b 1
)
echo.
echo === 2/2 QUANT VALIDATION ===
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\validate_scalping.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
if "%EXITCODE%"=="0" (
  echo SCALPING V1 QUANT PIPELINE: PASS
  echo Binance Demo is now unlocked for the current profile.
) else (
  echo SCALPING V1 QUANT PIPELINE: FAIL
  echo Demo remains locked.
)
pause
exit /b %EXITCODE%
