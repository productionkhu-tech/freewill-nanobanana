@echo off
cd /d "%~dp0"
echo ==========================================
echo   NanoBanana - Installation
echo ==========================================
echo.
echo  Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found.
    echo  Install Python 3.10+ first: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo.
echo  Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed.
    pause
    exit /b 1
)
echo.
echo ==========================================
echo   Installation complete!
echo   Next: run setup_env.bat to set API keys.
echo   Then: run start.bat to launch the app.
echo ==========================================
pause
