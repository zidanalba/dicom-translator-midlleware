@echo off
echo Activating virtual environment...
call venv\Scripts\activate

echo Starting Flask app...
python app.py
pause
