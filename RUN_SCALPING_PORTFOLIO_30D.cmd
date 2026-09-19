@echo off
setlocal
cd /d "%~dp0"
set "SIGNALGRID_SCALPING_DOWNLOAD_DAYS=30"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_scalping_portfolio_pipeline.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo Multi-symbol scalping pipeline exit code: %EXITCODE%
pause
exit /b %EXITCODE%
