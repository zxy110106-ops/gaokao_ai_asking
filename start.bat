@echo off
chcp 65001 >nul
title ZhangXuefeng Quiz
echo.
echo  ==========================================
echo    ZhangXuefeng - Intelligent Career Advisor
echo  ==========================================
echo.

echo  [1/4] Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo  [X] Python not found!
    pause
    exit /b 1
)

echo  [2/4] Installing dependencies...
pip install flask flask-cors anthropic ddgs -q
echo  [OK] Done

echo  [3/4] Loading config...
echo  [OK] Ready

echo  [4/4] Starting...
echo.
echo  ==========================================
echo    Open: http://localhost:5000
echo    Press Ctrl+C to stop
echo  ==========================================
echo.

start http://localhost:5000
python app.py
pause
