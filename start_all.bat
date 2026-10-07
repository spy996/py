@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [提示] 未找到运行环境，请先双击 install.bat 完成安装。
    pause
    exit /b 1
)

echo 正在启动系统（请保持本窗口与弹出的 3 个窗口打开）...
start "Backend-uvicorn" cmd /k "venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "Frontend-streamlit" cmd /k "venv\Scripts\python.exe -m streamlit run app.py"
start "QQ-Bot-gateway" cmd /k "venv\Scripts\python.exe qq_bot.py"

echo.
echo 后端已启动：http://127.0.0.1:8000
echo 前端界面：http://localhost:8501  （浏览器打开）
echo 若未配置 QQ 机器人，第三个窗口会提示后自动退出，属正常现象。
echo.
timeout /t 5 >nul
