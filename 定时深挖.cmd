@echo off
chcp 65001 >nul
rem 供 Windows 计划任务调用的无界面深挖入口：不 pause，输出追加到 data\logs\deepdive.log
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"

if not exist "data\logs" mkdir "data\logs"
>>"data\logs\deepdive.log" echo.
>>"data\logs\deepdive.log" echo ===== %date% %time% =====
".venv\Scripts\python.exe" -m processor.deepdive >>"data\logs\deepdive.log" 2>&1
set "RC=%ERRORLEVEL%"
>>"data\logs\deepdive.log" echo ===== exit=%RC% =====
exit /b %RC%
