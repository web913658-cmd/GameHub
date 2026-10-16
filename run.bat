@echo off
chcp 65001 >nul
title GameHub
cd /d "%~dp0"

echo.
echo   GameHub  ·  一站式游戏库启动器
echo   ------------------------------------
echo.

rem 优先使用 py 启动器，其次 python
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 GameHub.py
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python GameHub.py
    goto :eof
)

echo   [错误] 未检测到 Python。
echo   请先安装 Python 3.9+ ： https://www.python.org/downloads/
echo   或直接运行已打包的 GameHub.exe
echo.
pause
