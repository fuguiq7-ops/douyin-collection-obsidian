@echo off
chcp 65001 >nul
rem 供 Windows 计划任务调用的无界面提炼入口：不 pause，输出追加到 data\logs\quicknote.log
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist "data\logs" mkdir "data\logs"
>>"data\logs\quicknote.log" echo.
>>"data\logs\quicknote.log" echo ===== %date% %time% =====
".venv\Scripts\python.exe" -m processor.quicknote >>"data\logs\quicknote.log" 2>&1
set "RC=%ERRORLEVEL%"
>>"data\logs\quicknote.log" echo ===== exit=%RC% =====
exit /b %RC%
