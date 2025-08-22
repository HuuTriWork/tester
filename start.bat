@echo off
title Rise of Kingdoms Bot Launcher
color 0a

echo ======================================
echo      Rise of Kingdoms Bot - Start
echo ======================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [❌] Python chưa được cài đặt hoặc chưa có trong PATH.
    pause
    exit /b
)

python main.py

echo.
echo [⚠] Script đã kết thúc.
pause
