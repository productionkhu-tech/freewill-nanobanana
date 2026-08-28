@echo off
setlocal EnableDelayedExpansion EnableExtensions
chcp 65001 >nul
title 나노바나나 - 서버 주소 갱신

REM ============================================================
REM  공유기를 바꿨거나 이사한 뒤, 이 서버의 현재 주소를 전 직원에게
REM  알립니다. 직원은 아무것도 하지 않고, 앱을 다음에 켤 때 자동으로
REM  새 주소로 접속합니다.
REM
REM  터널(Tailscale)을 쓰고 있으면 주소가 바뀌지 않으므로 보통 이 파일을
REM  쓸 일이 없습니다. 사내망 IP 로 운영하거나 연결 방식을 바꿨을 때 씁니다.
REM ============================================================

cd /d "%~dp0"
set "EXE=%~dp0NanoBananaGateway.exe"

cls
echo.
echo  ============================================================
echo    서버 주소 갱신
echo  ============================================================
echo.

if not exist "%EXE%" (
  echo  [X] NanoBananaGateway.exe 가 이 폴더에 없습니다.
  echo.
  pause & exit /b 1
)

REM 게이트웨이가 살아 있어야 의미가 있다
powershell -NoProfile -Command "try { $null = Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 5; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo  [!] 게이트웨이가 응답하지 않습니다.
  echo      먼저 게이트웨이를 켜고 다시 실행하세요.
  echo.
  pause & exit /b 1
)
echo  [OK] 게이트웨이 동작 중
echo.

echo  현재 이 서버의 주소를 찾는 중...
echo.
"%EXE%" --publish
set "RC=%ERRORLEVEL%"
echo.

if "%RC%"=="0" (
  echo  ============================================================
  echo    완료 - 전 직원이 다음 실행부터 새 주소로 접속합니다.
  echo    아무에게도 알릴 필요 없습니다.
  echo  ============================================================
) else if "%RC%"=="2" (
  echo  ============================================================
  echo    자동 게시가 꺼져 있습니다.
  echo.
  echo    위에 표시된 주소를 개발자에게 알려주시면 반영됩니다.
  echo.
  echo    앞으로 이 버튼 하나로 끝내고 싶으시면
  echo    gateway_config.json 에 아래 한 줄을 추가하세요:
  echo.
  echo        "github_token": "ghp_...",
  echo.
  echo    ^(GitHub - Settings - Developer settings 에서
  echo      이 저장소의 Contents 쓰기 권한만 준 토큰을 만드세요^)
  echo  ============================================================
) else (
  echo  ============================================================
  echo    게시에 실패했습니다. 위 메시지를 확인해 주세요.
  echo    인터넷 연결이나 토큰 권한 문제일 수 있습니다.
  echo  ============================================================
)
echo.
pause
endlocal
