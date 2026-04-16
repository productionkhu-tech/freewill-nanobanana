@echo off
chcp 65001 >nul
echo ==========================================
echo   NanoBanana - Installation
echo ==========================================
echo.

echo  Python 확인 중...
python --version 2>nul
if errorlevel 1 (
    echo.
    echo  ERROR: Python이 설치되어 있지 않습니다.
    echo  Python 3.10 이상을 먼저 설치해주세요.
    echo  https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo  의존성 설치 중...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: 패키지 설치에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   설치 완료!
echo.
echo   다음 단계:
echo   1. setup_env.bat 실행 → API 키 등록
echo   2. python launcher.py → 앱 실행
echo ==========================================
pause
