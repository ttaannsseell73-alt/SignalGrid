@echo off
setlocal
cd /d "%~dp0"
set "SIGNALGRID_SCALPING_DOWNLOAD_DAYS=30"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\download_scalping_data.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo Scalping 30D data download exit code: %EXITCODE%
pause
exit /b %EXITCODE%
