@echo off
setlocal EnableDelayedExpansion EnableExtensions
chcp 65001 >nul
title 나노바나나 - 직접 키 모드로 복구

REM ============================================================
REM  게이트웨이를 쓰지 않고, 예전처럼 이 PC 의 키로 직접 호출합니다.
REM  게이트웨이 PC 가 꺼졌거나 문제가 있을 때 되돌리는 용도.
REM ============================================================

cls
echo.
echo  ============================================================
echo    나노바나나 - 직접 키 모드로 복구
echo  ============================================================
echo.

set "BAK=%NANOBANANA_REAL_KEY_BACKUP%"
if "!BAK!"=="" (
  echo  백업된 키가 없습니다. 진짜 GPT 키를 붙여넣으세요.
  echo  ^(비워두고 엔터를 치면 게이트웨이 설정만 해제합니다^)
  echo.
  set /p BAK="  키: "
) else (
  set "HEAD=!BAK:~0,14!"
  echo  백업된 키를 찾았습니다: !HEAD!...
)
echo.

if not "!BAK!"=="" (
  setx OPENAI_API_KEY "!BAK!" >nul
  echo  [OK] OPENAI_API_KEY 를 진짜 키로 복구
)

REM 게이트웨이 설정 해제 - 빈 값으로 두면 앱이 예전처럼 직접 호출
reg delete "HKCU\Environment" /v NANOBANANA_GATEWAY_URL /f >nul 2>&1
echo  [OK] 게이트웨이 설정 해제

set "GWJSON=%USERPROFILE%\.nanobanana\gateway.json"
if exist "!GWJSON!" (
  del /q "!GWJSON!" >nul 2>&1
  echo  [OK] 저장된 토큰 삭제
)

echo.
echo  ============================================================
echo    복구 완료 - 나노바나나를 껐다가 다시 켜세요.
echo    로그에 "OpenAI connected" 가 보이면 정상입니다.
echo  ============================================================
echo.
pause
endlocal
