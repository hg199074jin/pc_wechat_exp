@echo off
chcp 65001 >nul
echo [*] Checking port 5051...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5051.*LISTENING"') do (
    echo [*] Killing PID %%a on port 5051...
    taskkill /F /PID %%a 2>nul
)

echo [*] Starting WeChat EXP...
python src/main.py
pause
