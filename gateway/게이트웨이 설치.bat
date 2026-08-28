@echo off
setlocal EnableDelayedExpansion EnableExtensions
chcp 65001 >nul
title 나노바나나 게이트웨이 설치

REM ============================================================
REM  나노바나나 게이트웨이 - 상시 켜두는 PC 에 설치
REM
REM  이 PC 가 회사의 GPT 키를 대신 들고 있게 됩니다.
REM  직원 PC 에는 키가 없고, 이 PC 를 거쳐서만 생성됩니다.
REM
REM  필요한 것: 이 폴더에 NanoBananaGateway.exe 가 함께 있을 것.
REM  파이썬은 필요 없습니다.
REM ============================================================

set "HERE=%~dp0"
set "EXE=%HERE%NanoBananaGateway.exe"
set "CFG=%HERE%gateway_config.json"

cls
echo.
echo  ============================================================
echo    나노바나나 게이트웨이 설치
echo  ============================================================
echo.

if not exist "%EXE%" (
  echo  [X] NanoBananaGateway.exe 가 이 폴더에 없습니다.
  echo      EXE 와 이 bat 을 같은 폴더에 두고 다시 실행하세요.
  echo.
  pause & exit /b 1
)

if exist "%CFG%" (
  echo  [!] 이미 설정되어 있습니다.  %CFG%
  echo.
  set /p REDO="  다시 설정할까요? (y/N) "
  if /i not "!REDO!"=="y" goto :startup
  echo.
)

echo  1) 진짜 GPT 키를 붙여넣으세요.
echo     ^(OpenAI 콘솔의 서비스 계정 키, sk-svcacct- 로 시작^)
echo.
set /p REALKEY="  키: "
if "!REALKEY!"=="" ( echo  [X] 키가 비었습니다. & pause & exit /b 1 )
echo.

echo  2) 입장권을 붙여넣으세요.
echo     ^(직원 PC 에 이미 깔려 있는 예전 GPT 키.
echo      직원 PC 에서:  echo %%OPENAI_API_KEY%%  ^)
echo.
set /p TICKET="  입장권: "
if "!TICKET!"=="" ( echo  [X] 입장권이 비었습니다. & pause & exit /b 1 )
echo.

REM 관리자 키 자동 생성 (명단 조회/차단에 필요)
set "ADMINKEY="
for /f %%a in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"') do set "ADMINKEY=%%a"

REM 입장권 해시 (원문은 저장하지 않음)
for /f %%h in ('powershell -NoProfile -Command "$s=[Text.Encoding]::UTF8.GetBytes('!TICKET!'); $h=[Security.Cryptography.SHA256]::Create().ComputeHash($s); -join ($h ^| ForEach-Object { $_.ToString('x2') })"') do set "TICKETHASH=%%h"

REM 설정 파일 작성 (JSON)
> "%CFG%" echo {
>> "%CFG%" echo   "openai_key": "!REALKEY!",
>> "%CFG%" echo   "admin_key": "!ADMINKEY!",
>> "%CFG%" echo   "ticket_hashes": "!TICKETHASH!",
>> "%CFG%" echo   "data_dir": "%HERE:\=/%data",
>> "%CFG%" echo   "host": "0.0.0.0",
>> "%CFG%" echo   "port": 8787,
>> "%CFG%" echo   "openai_rpm": 60
>> "%CFG%" echo }

echo  [OK] 설정 저장 완료
echo.

:startup
REM ---- 자동 시작 등록 ----
REM 시작프로그램 폴더는 "로그인한 뒤"에만 실행됩니다. 윈도우 업데이트로
REM 재부팅되면 잠금화면에서 멈춰 있어 게이트웨이가 안 뜨고, 그 사이 전 직원이
REM GPT 를 못 씁니다. 그래서 부팅 시점에 뜨는 작업 스케줄러를 먼저 씁니다.
net session >nul 2>&1
if errorlevel 1 goto :nonadmin

schtasks /delete /tn "NanoBanana Gateway" /f >nul 2>&1
schtasks /create /tn "NanoBanana Gateway" /tr "\"%EXE%\"" /sc onstart /ru SYSTEM /rl highest /f >nul 2>&1
if errorlevel 1 goto :nonadmin
echo  [OK] 부팅 시 자동 실행 등록 - 로그인 안 해도 뜹니다
REM 시작프로그램 방식이 남아 있으면 두 개가 동시에 떠서 포트가 충돌합니다
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NanoBananaGateway.bat" >nul 2>&1

REM 절전으로 잠들면 서버도 멈춥니다
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1
powercfg /change monitor-timeout-ac 10 >nul 2>&1
echo  [OK] 절전 해제 - 화면만 10분 뒤 꺼지고 PC 는 계속 켜져 있습니다
goto :afterstartup

:nonadmin
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\NanoBananaGateway.bat" echo @echo off
>> "%STARTUP%\NanoBananaGateway.bat" echo start "" /min "%EXE%"
echo  [!] 관리자 권한이 아니라 "로그인 후 자동 실행" 으로만 등록했습니다.
echo      재부팅 후 로그인 전까지는 게이트웨이가 뜨지 않습니다.
echo      이 bat 을 우클릭 - 관리자 권한으로 실행 하면 부팅 시 자동 실행까지 됩니다.

:afterstartup
echo.

REM 방화벽 허용 (관리자 권한 있을 때만 성공)
netsh advfirewall firewall delete rule name="NanoBanana Gateway" >nul 2>&1
netsh advfirewall firewall add rule name="NanoBanana Gateway" dir=in action=allow protocol=TCP localport=8787 >nul 2>&1
if errorlevel 1 (
  echo  [!] 방화벽 규칙 자동 등록 실패 - 관리자 권한으로 실행하면 됩니다.
  echo      또는 게이트웨이 첫 실행 때 뜨는 창에서 "액세스 허용" 을 누르세요.
) else (
  echo  [OK] 방화벽 8787 포트 허용
)
echo.

start "" /min "%EXE%"
echo  [OK] 게이트웨이 시작
timeout /t 5 /nobreak >nul

REM 주소/관리자 키는 EXE 가 직접 출력한다. bat 안에서 PowerShell 로 값을 캐내던
REM 방식은 콘솔 코드페이지에 따라 빈 값이 나왔다.
"%EXE%" --info

echo.
echo  ============================================================
echo    위의 [ADDRESS] 주소를 개발자에게 알려주면
echo    앱에 넣어서 배포합니다. 직원은 앱만 업데이트하면 끝입니다.
echo.
echo    [ADMIN KEY] 는 따로 적어두세요 (명단 조회 / 차단에 필요).
echo    언제든 "정보 확인.bat" 으로 다시 볼 수 있습니다.
echo  ============================================================
echo.
pause
endlocal
