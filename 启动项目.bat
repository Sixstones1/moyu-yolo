@echo off
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%.venv\Scripts\pythonw.exe" (
    echo Missing .venv\Scripts\pythonw.exe
    pause
    exit /b 1
)

if not exist "%ROOT%app.py" (
    echo Missing app.py
    pause
    exit /b 1
)

wscript.exe "%ROOT%start_hidden.vbs"
exit /b 0
