@echo off
cd /d "%~dp0"
echo ==========================================
echo   NanoBanana - Environment Setup
echo ==========================================
echo.
echo  Register API keys as environment variables.
echo  Restart terminal/app after setup.
echo.

echo [1/4] Google Service Account JSON file path
echo   (Full path to your .json credentials file)
set /p CREDS_PATH="Path: "
if "%CREDS_PATH%"=="" (
    echo  Skipped. (Vertex AI will not be used)
    goto SKIP_VERTEX
)
if not exist "%CREDS_PATH%" (
    echo  ERROR: File not found. Skipping Vertex AI.
    goto SKIP_VERTEX
)
setx GOOGLE_APPLICATION_CREDENTIALS "%CREDS_PATH%" >nul
echo  [OK] GOOGLE_APPLICATION_CREDENTIALS set.

echo.
echo [2/4] Google Cloud Project ID
set /p PROJ_ID="Project ID: "
if "%PROJ_ID%"=="" (
    echo  Skipped.
    goto SKIP_VERTEX
)
setx NANOBANANA_PROJECT_ID "%PROJ_ID%" >nul
echo  [OK] NANOBANANA_PROJECT_ID set.

echo.
echo [3/4] Vertex AI Location (press Enter for "global")
set /p LOC="Location: "
if "%LOC%"=="" set LOC=global
setx NANOBANANA_LOCATION "%LOC%" >nul
echo  [OK] NANOBANANA_LOCATION = %LOC%

:SKIP_VERTEX
echo.
echo [4/4] AI Studio API Key
set /p STUDIO_KEY="API Key: "
if "%STUDIO_KEY%"=="" (
    echo  Skipped. (AI Studio will not be used)
    goto DONE
)
setx NANOBANANA_STUDIO_KEY "%STUDIO_KEY%" >nul
echo  [OK] NANOBANANA_STUDIO_KEY set.

:DONE
echo.
echo ==========================================
echo   Done! Restart your terminal or app.
echo ==========================================
pause
