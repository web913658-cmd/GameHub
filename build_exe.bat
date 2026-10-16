@echo off
chcp 65001 >nul
title GameHub 打包
cd /d "%~dp0"

echo.
echo   ============================================
echo     GameHub  ·  打包为独立 exe
echo   ============================================
echo.

rem ---- 1. 找 Python ----
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )
if "%PY%"=="" (
    echo   [错误] 未检测到 Python，请先安装 Python 3.9+
    echo          https://www.python.org/downloads/
    pause & exit /b 1
)
echo   [1/4] 使用 Python: %PY%

rem ---- 2. 建虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
    echo   [2/4] 创建虚拟环境 .venv ...
    %PY% -m venv .venv
) else (
    echo   [2/4] 复用已有虚拟环境 .venv
)
set VPY=.venv\Scripts\python.exe

rem ---- 3. 装依赖 ----
echo   [3/4] 安装 PyInstaller ...
"%VPY%" -m pip install --upgrade pip -q
"%VPY%" -m pip install pyinstaller -q

rem 可选：安装 pywebview 可获得真正的原生窗口（失败不影响打包）
"%VPY%" -m pip install pywebview -q 2>nul && (
    echo         pywebview 已安装，将使用原生窗口
) || (
    echo         pywebview 未安装，将使用 Edge/Chrome 应用模式窗口
)

rem ---- 4. 打包 ----
echo   [4/4] 开始打包 ...
"%VPY%" -m PyInstaller build\GameHub.spec --noconfirm --clean

echo.
if exist "dist\GameHub.exe" (
    echo   ============================================
    echo     ✓ 打包完成： dist\GameHub.exe
    echo   ============================================
    for %%A in ("dist\GameHub.exe") do echo     体积： %%~zA 字节
    echo.
    echo     双击 dist\GameHub.exe 即可运行，无需安装 Python。
) else (
    echo   [失败] 未生成 exe，请查看上方日志。
)
echo.
pause
