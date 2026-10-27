@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONW=%ROOT%.venv\Scripts\pythonw.exe"
set "APP=%ROOT%app.py"

if not exist "%PYTHONW%" (
    echo Python runtime not found: "%PYTHONW%"
    echo Run the project environment setup first.
    pause
    exit /b 1
)

if not exist "%APP%" (
    echo Application entry point not found: "%APP%"
    pause
    exit /b 1
)

cd /d "%ROOT%"
start "" "%PYTHONW%" "%APP%"
exit /b
