@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :need_setup
rem 速查提炼：已转写条目 -> 大模型提炼速查稿；参数透传，如 --id <aweme_id> --dry-run
".venv\Scripts\python.exe" -m processor.quicknote %*
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo [提示] 退出码 %EXITCODE%（1=出错；详细见 data\logs）
pause
exit /b %EXITCODE%

:need_setup
echo 请先双击「准备.cmd」完成安装。
pause
exit /b 1
