@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 研发知识智能管理平台 - 一键安装

echo ==========================================================
echo    研发知识智能管理平台  一键安装
echo ==========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本。
    echo 下载地址：https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH" 选项。
    echo.
    pause
    exit /b 1
)

echo [1/3] 已检测到 Python：
python --version
echo.

if not exist "venv\Scripts\python.exe" (
    echo [2/3] 正在创建虚拟环境 venv ...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败，请检查 Python 安装是否完整。
        pause
        exit /b 1
    )
) else (
    echo [2/3] 虚拟环境 venv 已存在，跳过创建。
)
echo.

echo [3/3] 正在安装依赖包（首次约 5~15 分钟，请耐心等待，切勿关闭窗口）...
venv\Scripts\python.exe -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

if %errorlevel% neq 0 (
    echo.
    echo [错误] 依赖安装失败，请检查网络后重试；若持续失败可去掉镜像源再试。
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo    安装完成！
echo    使用步骤：
echo      1) 若尚未配置，请编辑 .env 填写 DeepSeek API Key
echo      2) 双击 start_all.bat 启动系统
echo      3) 浏览器打开 http://localhost:8501
echo ==========================================================
echo.
pause
