@echo off
title Telegram Bot Can Cuoc Tu Dong
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo   HE THONG TELEGRAM BOT TU DONG CAN VA CHUYEN CUOC
echo ========================================================
echo   Dang khoi dong may chu backend tren cong 5005...
echo   Giao dien web quan ly tai: http://localhost/lk
echo.

python app.py
pause
