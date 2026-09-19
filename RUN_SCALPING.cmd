@echo off
setlocal
cd /d "%~dp0"
call "%~dp0RUN_SCALPING_PAPER.cmd"
exit /b %ERRORLEVEL%
