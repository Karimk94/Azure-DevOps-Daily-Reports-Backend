@echo off

:: 1. Create Venv if it doesn't exist
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

:: 2. Install from local wheels folder
echo Installing requirements from local 'wheels' folder...
venv\Scripts\python.exe -m pip install --no-index --find-links=wheels -r requirements.txt

echo.
echo Installation complete!
echo To run your app:
echo venv\Scripts\python.exe app.py
pause
