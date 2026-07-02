@echo off
title NIRMAN Data Automation
:: Change to the folder where this .bat file lives, regardless of where it was called from
cd /d "%~dp0"

echo ============================================================
echo   NIRMAN Data Automation -- Windows Launcher
echo ============================================================
echo.

:: Check if python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not on your PATH.
    echo Download Python at https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Run the universal launcher
python start.py

:: If the server exits with an error, keep the window open
if errorlevel 1 (
    echo.
    echo Server exited with an error. See messages above.
    pause
)
