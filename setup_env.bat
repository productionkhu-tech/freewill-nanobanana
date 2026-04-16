@echo off
chcp 65001 >nul
echo ==========================================
echo   NanoBanana - Environment Setup
echo ==========================================
echo.
echo  API 키를 환경변수로 등록합니다.
echo  설정 후 터미널/앱을 재시작해야 적용됩니다.
echo.

echo [1/4] Google Service Account JSON 파일 경로
echo   (서비스 계정 .json 파일의 전체 경로를 입력하세요)
set /p CREDS_PATH="Path: "
if "%CREDS_PATH%"=="" (
    echo  건너뜁니다 (Vertex AI 사용 안 함^)
    goto :SKIP_VERTEX
)
if not exist "%CREDS_PATH%" (
    echo  ERROR: 파일을 찾을 수 없습니다: %CREDS_PATH%
    echo  Vertex AI 설정을 건너뜁니다.
    goto :SKIP_VERTEX
)
setx GOOGLE_APPLICATION_CREDENTIALS "%CREDS_PATH%" >nul
echo  [OK] GOOGLE_APPLICATION_CREDENTIALS 등록 완료

echo.
echo [2/4] Google Cloud Project ID
set /p PROJ_ID="Project ID: "
if "%PROJ_ID%"=="" (
    echo  건너뜁니다.
    goto :SKIP_VERTEX
)
setx NANOBANANA_PROJECT_ID "%PROJ_ID%" >nul
echo  [OK] NANOBANANA_PROJECT_ID 등록 완료

echo.
echo [3/4] Vertex AI Location (Enter 누르면 "global")
set /p LOC="Location: "
if "%LOC%"=="" set LOC=global
setx NANOBANANA_LOCATION "%LOC%" >nul
echo  [OK] NANOBANANA_LOCATION = %LOC%

:SKIP_VERTEX
echo.
echo [4/4] AI Studio API Key
echo   (Google AI Studio에서 발급받은 API 키)
set /p STUDIO_KEY="API Key: "
if "%STUDIO_KEY%"=="" (
    echo  건너뜁니다 (AI Studio 사용 안 함^)
    goto :DONE
)
setx NANOBANANA_STUDIO_KEY "%STUDIO_KEY%" >nul
echo  [OK] NANOBANANA_STUDIO_KEY 등록 완료

:DONE
echo.
echo ==========================================
echo   환경변수 설정 완료!
echo   터미널/앱을 재시작하면 적용됩니다.
echo ==========================================
pause
