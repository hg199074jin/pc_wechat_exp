@echo off
chcp 65001 >nul
title WeChat EXP
cd /d "%~dp0"

echo.
echo ============================================================
echo   WeChat EXP
echo ============================================================
echo.
echo   [1] Backup (requires WeChat login)
echo   [2] Start Chat Viewer
echo   [3] Backup then Start
echo   [0] Exit
echo.
set /p choice="Select (0-3): "

if "%choice%"=="1" goto backup
if "%choice%"=="2" goto serve
if "%choice%"=="3" goto both
if "%choice%"=="0" goto end
echo Invalid choice
pause
goto end

:backup
echo.
echo [*] Starting backup... Make sure WeChat is logged in.
echo.
.venv\Scripts\python.exe src\main.py backup
echo.
echo [*] Backup complete!
echo.
pause
goto serve

:both
echo.
echo [*] Starting backup... Make sure WeChat is logged in.
echo.
.venv\Scripts\python.exe src\main.py backup
echo.
echo [*] Backup complete, starting viewer...
echo.

:serve
set PORT=5051
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%.*LISTENING" 2^>nul') do (
    echo [*] Killing old process PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)
echo.
echo ============================================================
echo   Chat Viewer: http://127.0.0.1:%PORT%
echo   DO NOT close this window
echo ============================================================
echo.
.venv\Scripts\python.exe src\main.py serve --port %PORT%
pause
goto end

:end
