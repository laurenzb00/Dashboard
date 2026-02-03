@echo off
REM Startup script for Windows

cd /d "%~dp0"

REM Activate venv
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual Environment not found!
    echo Please create it: python -m venv .venv
    pause
    exit /b 1
)

REM Add src to PYTHONPATH
set PYTHONPATH=%CD%\src;%PYTHONPATH%

echo Starting Application...
python src\main.py
pause
