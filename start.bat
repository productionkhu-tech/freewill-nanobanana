@echo off
cd /d "%~dp0"
title NanoBanana
python launcher.py
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to start. Run install.bat first.
    pause
)
