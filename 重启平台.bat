@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   重启研发知识智能管理平台（含网络通讯台账）
echo ============================================
echo.
echo [1/3] 停止旧的 uvicorn / streamlit 进程...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'uvicorn|streamlit' } | ForEach-Object { Write-Host ('  kill PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >nul

echo.
echo [2/3] 检查 8000 / 8501 端口是否已释放...
netstat -ano | findstr ":8000"
netstat -ano | findstr ":8501"
echo （上面没有输出 = 端口已释放，正常）

echo.
echo [3/3] 启动后端 / 前端 / QQ 机器人...
call "%~dp0start_all.bat"

echo.
echo 启动命令已发出。请等待 5 秒，然后刷新 http://localhost:8501
echo 如果后端窗口报错，或端口仍被占用，请把本窗口和后端窗口截图发我。
pause
