@echo off
REM Gateway info - address, admin key, current state.
REM Safe to run any time: reads only, changes nothing.
cd /d "%~dp0"
NanoBananaGateway.exe --info
echo.
pause
