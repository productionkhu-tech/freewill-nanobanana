@echo off
chcp 65001 >nul
title NanoBanana
cd /d "%~dp0"
python launcher.py
if errorlevel 1 (
    echo.
    echo  Python이 설치되어 있지 않거나 오류가 발생했습니다.
    echo  먼저 install.bat을 실행해주세요.
    pause
)
