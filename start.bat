@echo off
echo ======================================
echo   Starting DICOM Translator Service
echo ======================================

:: Step 1: Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

:: Step 2: Start the Flask app
echo Starting Flask app...
python app.py
if errorlevel 1 (
    echo [ERROR] Failed to start Flask app.
    pause
    exit /b 1
)

pause
