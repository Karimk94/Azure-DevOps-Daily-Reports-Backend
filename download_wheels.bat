@echo off
if not exist requirements.txt (
    echo Error: requirements.txt not found!
    pause
    exit /b
)

if not exist wheels mkdir wheels

echo Downloading packages to 'wheels' folder...
pip download -r requirements.txt -d wheels

echo.
echo Download complete! 
echo Copy the 'wheels' folder and 'requirements.txt' to your offline server.
pause
