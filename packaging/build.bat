@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

rem ============================================================
rem  科技项目管理系统 · 一键构建脚本（Windows）
rem  产出：科技项目管理系统安装包.exe
rem  前置：已安装 Inno Setup 6（ISCC.exe），能联网（首次需下载 Python 3.12.10）
rem ============================================================

set "SCRIPT_DIR=%~dp0"
set "ROOT=%~dp0.."
set "PY_VERSION=3.12.10"
set "PY_INSTALLER=python-%PY_VERSION%-amd64.exe"
set "RUNTIME=%SCRIPT_DIR%runtime"
set "STAGE=%SCRIPT_DIR%dist\科技项目管理系统"
set "LAUNCHER_EXE=%SCRIPT_DIR%dist\科技项目管理系统.exe"
set "OUTPUT_EXE=%SCRIPT_DIR%科技项目管理系统安装包.exe"

echo.
echo ============================================================
echo  [构建] 科技项目管理系统 一键打包
echo ============================================================

rem ---------- [1/8] 准备便携 Python 运行时 ----------
echo.
echo [1/8] 准备便携 Python %PY_VERSION% 运行时 ...
set "PY_OK="
if exist "%RUNTIME%\python.exe" (
  "%RUNTIME%\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY_OK=1"
)
if defined PY_OK (
  echo      已存在可用的 runtime 解释器，跳过安装。
  goto :py_ready
)

if not exist "%SCRIPT_DIR%%PY_INSTALLER%" (
  echo      未找到本地安装器，开始从 python.org 下载 %PY_INSTALLER% ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PY_VERSION%/%PY_INSTALLER%' -OutFile '%SCRIPT_DIR%%PY_INSTALLER%'" >nul 2>&1
  if errorlevel 1 (
    echo      [错误] 下载 Python 安装器失败。请手动下载后放到 %SCRIPT_DIR% 后重试。
    exit /b 1
  )
)

echo      静默安装 Python 到 runtime\ ...
"%SCRIPT_DIR%%PY_INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%RUNTIME%" ^
  Include_pip=1 Include_tcltk=1 Include_test=0 Include_doc=0 Include_launcher=0 ^
  PrependPath=0 Shortcuts=0 AssociateFiles=0 >nul 2>&1
if not exist "%RUNTIME%\python.exe" (
  echo      [错误] Python 运行时安装失败，请检查磁盘空间与网络后重试。
  exit /b 1
)
echo      安装完成：%RUNTIME%\python.exe

:py_ready

rem ---------- [2/8] 升级 pip ----------
echo.
echo [2/8] 升级 pip ...
"%RUNTIME%\python.exe" -m pip install --upgrade pip --no-warn-script-location >nul 2>&1
if errorlevel 1 (
  echo      [警告] pip 升级失败，继续使用现有版本。
)

rem ---------- [3/8] 安装运行时依赖 ----------
echo.
echo [3/8] 安装运行时依赖（requirements-runtime.txt，体积较大，请耐心等待）...
"%RUNTIME%\python.exe" -m pip install -r "%SCRIPT_DIR%requirements-runtime.txt" --no-warn-script-location >nul 2>&1
if errorlevel 1 (
  echo      [错误] 运行时依赖安装失败，请检查网络后重试。
  exit /b 1
)
echo      运行时依赖安装完成。

rem ---------- [4/8] 安装构建期依赖 ----------
echo.
echo [4/8] 安装构建期依赖（requirements-build.txt）...
"%RUNTIME%\python.exe" -m pip install -r "%SCRIPT_DIR%requirements-build.txt" --no-warn-script-location >nul 2>&1
if errorlevel 1 (
  echo      [错误] 构建期依赖安装失败（pystray/pyinstaller）。
  exit /b 1
)
echo      构建期依赖安装完成。

rem ---------- [5/8] 拷贝业务源码（排除清单） ----------
echo.
echo [5/8] 拷贝业务源码（按 exclude.lst 排除业务数据）...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
"%RUNTIME%\python.exe" "%SCRIPT_DIR%build_copy.py" --src "%ROOT%" --dst "%STAGE%\app" --exclude "%SCRIPT_DIR%exclude.lst"
if errorlevel 1 (
  echo      [错误] 源码拷贝失败。
  exit /b 1
)

rem 安装目录根：_packaged 标记 + 首启向导 + .env 模板
echo packaged>"%STAGE%\_packaged"
copy /Y "%SCRIPT_DIR%setup_wizard.py" "%STAGE%\" >nul
copy /Y "%SCRIPT_DIR%.env.example" "%STAGE%\" >nul
echo      源码与配置模板已就绪。

rem ---------- [6/8] PyInstaller 打 launcher.exe ----------
echo.
echo [6/8] PyInstaller 打包托盘启动器 ...
pushd "%SCRIPT_DIR%"
"%RUNTIME%\python.exe" -m PyInstaller --clean --noconfirm launcher.spec >nul 2>&1
if errorlevel 1 (
  popd
  echo      [错误] PyInstaller 打包 launcher 失败，请查看控制台/日志。
  exit /b 1
)
popd
if not exist "%LAUNCHER_EXE%" (
  echo      [错误] 未找到 %LAUNCHER_EXE%。
  exit /b 1
)
copy /Y "%LAUNCHER_EXE%" "%STAGE%\" >nul
echo      启动器打包完成：%STAGE%\科技项目管理系统.exe

rem ---------- [7/8] 拷贝便携 Python 运行时 ----------
echo.
echo [7/8] 拷贝便携 Python 运行时（体积较大，请耐心等待）...
robocopy "%RUNTIME%" "%STAGE%\runtime" /E ^
  /XD __pycache__ PyInstaller pyinstaller_hooks_contrib altgraph pefile pystray ^
  /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if errorlevel 8 (
  echo      [错误] 运行时拷贝失败（robocopy 退出码 %ERRORLEVEL%）。
  exit /b 1
)
echo      运行时拷贝完成。

rem ---------- [8/8] ISCC 编译安装程序 ----------
echo.
echo [8/8] ISCC 编译安装程序 ...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo      [错误] 未找到 Inno Setup 6 的 ISCC.exe。
  echo             请从 https://jrsoftware.org/isinfo.php 下载安装后重试。
  exit /b 1
)
"%ISCC%" /O"%SCRIPT_DIR%" "%SCRIPT_DIR%installer.iss"
if errorlevel 1 (
  echo      [错误] ISCC 编译失败，请检查 installer.iss 与暂存目录。
  exit /b 1
)

echo.
echo ============================================================
echo  构建完成！
echo  安装包：%OUTPUT_EXE%
echo ============================================================
endlocal
exit /b 0
