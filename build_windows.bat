@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Portfolio Builder - Windows 一键打包
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 .venv，请先执行：
    echo        python -m venv .venv
    echo        .venv\Scripts\python.exe -m pip install PyQt5
    pause
    exit /b 1
)

echo [1/3] 安装/更新 PyInstaller ...
".venv\Scripts\python.exe" -m pip install -q "pyinstaller>=6.0"
if errorlevel 1 ( echo [错误] PyInstaller 安装失败 & pause & exit /b 1 )

echo [2/3] 打包（首次约 1~3 分钟）...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist --workpath build macos\PortfolioBuilder.spec
if errorlevel 1 ( echo [错误] 打包失败 & pause & exit /b 1 )

echo [3/3] 完成
echo.
echo   产物: %cd%\dist\PortfolioBuilder\PortfolioBuilder.exe
echo.
pause
