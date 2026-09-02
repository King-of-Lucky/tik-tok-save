@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python 3 was not found. Install Python 3 from python.org and enable "Add Python to PATH".
        pause
        exit /b 1
    )
)

echo Updating dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo.
".venv\Scripts\python.exe" -c "import yt_dlp; print('Installed yt-dlp version:', yt_dlp.version.__version__)"
echo.

start "" ".venv\Scripts\pythonw.exe" launcher.py
endlocal
