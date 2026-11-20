@echo off
chcp 65001 >nul
rem 供 Windows 计划任务调用的全链路入口：同步 -> 深挖 -> 提炼 -> 推送速查，顺序执行，不 pause
rem 规则：同步失败即中止（后面三步无意义）；深挖/提炼/推送失败只记录，不阻塞后续步骤，末尾返回首个失败码
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "LOG=data\logs\chain.log"
set "FAIL="
if not exist "data\logs" mkdir "data\logs"

>>"%LOG%" echo.
>>"%LOG%" echo ===== %date% %time% 全链路开始 =====

rem ---------- 1/4 同步 ----------
>>"%LOG%" echo --- 1/4 同步 processor.sync ---
"%PY%" -m processor.sync >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>>"%LOG%" echo --- 1/4 同步 exit=%RC% ---
if not "%RC%"=="0" goto :abort_sync

rem ---------- 2/4 深挖 ----------
>>"%LOG%" echo --- 2/4 深挖 processor.deepdive ---
"%PY%" -m processor.deepdive >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>>"%LOG%" echo --- 2/4 深挖 exit=%RC% ---
if not "%RC%"=="0" if not defined FAIL set "FAIL=%RC%"

rem ---------- 3/4 提炼 ----------
>>"%LOG%" echo --- 3/4 提炼 processor.quicknote ---
"%PY%" -m processor.quicknote >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>>"%LOG%" echo --- 3/4 提炼 exit=%RC% ---
if not "%RC%"=="0" if not defined FAIL set "FAIL=%RC%"

rem ---------- 4/4 推送速查 ----------
>>"%LOG%" echo --- 4/4 推送 processor.feishu --quick ---
"%PY%" -m processor.feishu --quick >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>>"%LOG%" echo --- 4/4 推送 exit=%RC% ---
if not "%RC%"=="0" if not defined FAIL set "FAIL=%RC%"

if defined FAIL (
    >>"%LOG%" echo ===== %date% %time% 全链路结束：有步骤失败，首个失败码=%FAIL% =====
    exit /b %FAIL%
)
>>"%LOG%" echo ===== %date% %time% 全链路结束：四步全部成功 =====
exit /b 0

:abort_sync
>>"%LOG%" echo [中止] 同步失败（exit=%RC%），深挖/提炼/推送本次跳过
>>"%LOG%" echo ===== %date% %time% 全链路结束：同步失败 exit=%RC% =====
exit /b %RC%
