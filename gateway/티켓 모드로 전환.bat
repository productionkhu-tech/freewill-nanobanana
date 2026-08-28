@echo off
setlocal EnableDelayedExpansion EnableExtensions
chcp 65001 >nul
title 나노바나나 - 티켓 모드로 전환

REM ============================================================
REM  작업용 PC 를 게이트웨이에 연결합니다.
REM
REM  이 PC 에서 진짜 GPT 키를 걷어내고, 예전 키(입장권)만 남깁니다.
REM  앱이 그 입장권으로 스스로 등록하고 토큰을 받아 씁니다.
REM
REM  직원 PC 는 예전 키가 이미 들어 있으므로 이 bat 없이
REM  게이트웨이 주소만 설정하면 됩니다.
REM ============================================================

cls
echo.
echo  ============================================================
echo    나노바나나 - 티켓 모드로 전환
echo  ============================================================
echo.

REM 현재 상태
set "CUR=%OPENAI_API_KEY%"
if "!CUR!"=="" (
  echo   현재 OPENAI_API_KEY : ^(없음^)
) else (
  set "HEAD=!CUR:~0,14!"
  echo   현재 OPENAI_API_KEY : !HEAD!...
)
echo.

echo  1) 게이트웨이 주소를 입력하세요.
echo     ^(게이트웨이 설치 때 안내된 주소. 예: http://192.168.0.50:8787^)
echo.
set /p GWURL="  주소: "
if "!GWURL!"=="" ( echo  [X] 주소가 비었습니다. & pause & exit /b 1 )
echo.

echo  2) 입장권을 붙여넣으세요.
echo     ^(예전 GPT 키. 아직 새 키를 설치하지 않은 직원 PC 에서:
echo      echo %%OPENAI_API_KEY%%  ^)
echo.
set /p TICKET="  입장권: "
if "!TICKET!"=="" ( echo  [X] 입장권이 비었습니다. & pause & exit /b 1 )
echo.

REM 연결 확인부터
echo  게이트웨이 연결 확인 중...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest '!GWURL!/health' -UseBasicParsing -TimeoutSec 8; Write-Host '  [OK]' $r.Content; exit 0 } catch { Write-Host '  [X] 연결 실패 -' $_.Exception.Message; exit 1 }"
if errorlevel 1 (
  echo.
  echo  주소가 맞는지, 게이트웨이 PC 가 켜져 있는지 확인하세요.
  echo  설정을 저장하지 않고 종료합니다.
  echo.
  pause & exit /b 1
)
echo.

REM 진짜 키 백업 후 교체
if not "!CUR!"=="" (
  setx NANOBANANA_REAL_KEY_BACKUP "!CUR!" >nul
  echo  [OK] 기존 키를 NANOBANANA_REAL_KEY_BACKUP 에 백업
)
setx OPENAI_API_KEY "!TICKET!" >nul
echo  [OK] OPENAI_API_KEY 를 입장권으로 교체
setx NANOBANANA_GATEWAY_URL "!GWURL!" >nul
echo  [OK] 게이트웨이 주소 등록

REM 이전에 받은 토큰이 있으면 지워서 새로 등록되게
set "GWJSON=%USERPROFILE%\.nanobanana\gateway.json"
if exist "!GWJSON!" (
  del /q "!GWJSON!" >nul 2>&1
  echo  [OK] 이전 토큰 삭제 - 다음 실행 때 새로 등록됩니다
)

echo.
echo  ============================================================
echo    전환 완료
echo.
echo    나노바나나를 완전히 껐다가 다시 켜세요.
echo.
echo    확인할 것:
echo      - 하단 OpenAI 점이 초록
echo      - 로그에 "gateway: enrolled as ..." 와
echo        "OpenAI via gateway" 가 보임
echo      - GPT Image 2 로 실제 생성됨
echo.
echo    되돌리려면 "직접 키 모드로 복구.bat" 을 실행하세요.
echo  ============================================================
echo.
pause
endlocal
