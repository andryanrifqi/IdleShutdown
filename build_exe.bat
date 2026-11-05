@echo off
rem Builds dist\IdleShutdown.exe with PyInstaller (one file, no console).
rem Requires: py -m pip install pyinstaller
cd /d "%~dp0"
py -3 -m PyInstaller --onefile --noconsole --clean --name IdleShutdown idle_shutdown_app.pyw
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)
echo Built dist\IdleShutdown.exe
