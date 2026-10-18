@echo off
rem ============================================================
rem  Portfolio Builder · 一键启动（使用项目自带的 Python 3.12 环境）
rem ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [x] 未找到 .venv 环境，请先执行：
    echo     uv venv --python 3.12 .venv
    echo     uv pip install --python .venv\Scripts\python.exe PyQt5
    pause
    exit /b 1
)

".venv\Scripts\python.exe" portfolio_builder.py
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
    echo.
    echo [x] 程序异常退出，错误码 %EXITCODE%
    pause
)
