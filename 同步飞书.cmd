@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :need_setup
rem 旧通道：推送原始笔记到知识库；速查草稿请用「推送速查.cmd」。参数透传 --id <id> --force --dry-run
".venv\Scripts\python.exe" -m processor.feishu --raw %*
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo [提示] 退出码 %EXITCODE%（1=出错；详细见 data\logs）
pause
exit /b %EXITCODE%

:need_setup
echo 请先双击「准备.cmd」完成安装。
pause
exit /b 1
