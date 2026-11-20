@echo off
chcp 65001 >nul
rem 供 Windows 计划任务调用的无界面飞书推送入口：不 pause，输出追加到 data\logs\feishu.log
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist "data\logs" mkdir "data\logs"
>>"data\logs\feishu.log" echo.
>>"data\logs\feishu.log" echo ===== %date% %time% =====
".venv\Scripts\python.exe" -m processor.feishu --quick >>"data\logs\feishu.log" 2>&1
set "RC=%ERRORLEVEL%"
>>"data\logs\feishu.log" echo ===== exit=%RC% =====
exit /b %RC%
