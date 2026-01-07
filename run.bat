@echo off
title EKG Demo Launcher
echo Starting EKG demo environment...
echo ================================

REM ---- 1. Start Laravel server ----
echo Starting Laravel backend...
start cmd /k "cd C:\Users\zmuha\Documents\it-type-shit\dicom-translator-midlleware\his-demo && php artisan serve --host=0.0.0.0"

REM ---- 1. Start Vite frontend ----
echo Starting Vite frontend...
start cmd /k "cd C:\Users\zmuha\Documents\it-type-shit\dicom-translator-midlleware\his-demo && npm run dev"

REM ---- 2. Start DICOM Translator ----
echo Starting DICOM Translator Service...
start cmd /k "cd C:\Users\zmuha\Documents\it-type-shit\dicom-translator-midlleware && venv\Scripts\activate && python app.py"

REM ---- 3. Start Orthanc (if not running automatically) ----
echo Checking Orthanc...
tasklist /FI "IMAGENAME eq Orthanc.exe" | find /I "Orthanc.exe" > nul
if %errorlevel%==1 (
    echo Starting Orthanc...
    start "" "C:\Program Files\Orthanc Server\Orthanc.exe"
) else (
    echo Orthanc already running
)

REM ---- 4. Open web browser ----
echo Opening browser...
start http://127.0.0.1:8000

echo ================================
echo All services started!
pause
