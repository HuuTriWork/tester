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

echo [✔] Python đã sẵn sàng.

echo.
echo [🔄] Đang kiểm tra và cài đặt thư viện...
pip install --upgrade pip
pip install opencv-python numpy PyQt5 requests pyperclip

echo.
echo [✔] Tất cả thư viện đã được cài đặt.

echo.
echo [🚀] Đang chạy main.py ...
python main.py

echo.
echo [⚠] Script đã kết thúc.
pause
