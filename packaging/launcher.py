# -*- coding: utf-8 -*-
"""科技项目管理系统 · Windows 托盘启动器。

职责（按执行顺序）：
  1) 单实例互斥（Windows 命名互斥量优先，锁文件兜底）
  2) 计算 INSTALL_DIR / DATA_DIR，并确保数据/日志目录存在
  3) 若 DATA_DIR/.env 缺失管理员密码配置，则以子进程拉起首启向导 setup_wizard.py
  4) 用便携 runtime\\python.exe 拉起后端 uvicorn(:8000) 与前端 streamlit(:8501)
  5) 端口就绪探测（≤60s）后打开默认浏览器
  6) 显示托盘菜单（打开界面 / 停止服务 / 退出）；退出时终止子进程树并释放锁

说明：本模块不 import 项目的 config 等业务模块，路径解析逻辑独立复制一份，
避免把启动器与业务源码耦合、也便于用 PyInstaller 单独冻结。
"""
import os
import sys
import time
import threading
import subprocess
import webbrowser
import urllib.request
import urllib.error

import pystray
from PIL import Image, ImageDraw

APP_NAME = "科技项目管理系统"
BACKEND_PORT = 8000
FRONTEND_PORT = 8501
FRONTEND_URL = "http://localhost:%d" % FRONTEND_PORT
MUTEX_NAME = "Local\\RDPlatform.%s.Launcher" % APP_NAME

IS_FROZEN = bool(getattr(sys, "frozen", False))
if IS_FROZEN:
    INSTALL_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# 日志（windowed 模式无控制台，全部落盘到 DATA_DIR/logs/launcher.log）
# ---------------------------------------------------------------------------
_LOG_HANDLE = None


def log(msg: str) -> None:
    """把启动器自身日志追加写入日志文件，失败静默。"""
    global _LOG_HANDLE
    try:
        if _LOG_HANDLE is None:
            log_dir = os.path.join(resolve_data_dir(), "logs")
            os.makedirs(log_dir, exist_ok=True)
            _LOG_HANDLE = open(os.path.join(log_dir, "launcher.log"), "a", encoding="utf-8")
        _LOG_HANDLE.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        _LOG_HANDLE.flush()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 路径解析（与 config._resolve_data_dir 保持一致）
# ---------------------------------------------------------------------------
def resolve_data_dir() -> str:
    env = os.getenv("RD_DATA_DIR")
    if env:
        return env
    if os.path.exists(os.path.join(INSTALL_DIR, "_packaged")):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "RDPlatform", "data")
    return INSTALL_DIR


def runtime_python() -> str:
    """便携运行时 python.exe 路径；开发态回退到当前解释器。"""
    cand = os.path.join(INSTALL_DIR, "runtime", "python.exe")
    if os.path.exists(cand):
        return cand
    if not IS_FROZEN:
        return sys.executable
    return cand


def app_dir() -> str:
    """业务源码目录：安装态为 {INSTALL_DIR}/app，开发态回退到上一级。"""
    cand = os.path.join(INSTALL_DIR, "app")
    if os.path.exists(os.path.join(cand, "main.py")):
        return cand
    parent = os.path.dirname(INSTALL_DIR)
    if os.path.exists(os.path.join(parent, "main.py")):
        return parent
    return cand


def setup_wizard_path() -> str:
    return os.path.join(INSTALL_DIR, "setup_wizard.py")


# ---------------------------------------------------------------------------
# 单实例互斥
# ---------------------------------------------------------------------------
class SingleInstance:
    """Windows 命名互斥量优先；非 Windows 或失败时用锁文件兜底。"""

    def __init__(self, name: str = MUTEX_NAME):
        self._mutex_handle = None
        self._lock_path = None
        self._lock_fd = None
        self.acquired = False
        self._try_acquire(name)

    def _try_acquire(self, name: str) -> None:
        if os.name == "nt" and self._acquire_win_mutex(name):
            self.acquired = True
            return
        self.acquired = self._acquire_lock_file()

    def _acquire_win_mutex(self, name: str) -> bool:
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            CreateMutexW = k32.CreateMutexW
            CreateMutexW.restype = wintypes.HANDLE
            CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            handle = CreateMutexW(None, False, name)
            if not handle:
                return False
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                k32.CloseHandle(handle)
                return False
            self._mutex_handle = handle
            return True
        except Exception:  # noqa: BLE001
            return False

    def _acquire_lock_file(self) -> bool:
        lock_dir = resolve_data_dir()
        try:
            os.makedirs(lock_dir, exist_ok=True)
            self._lock_path = os.path.join(lock_dir, "launcher.lock")
            self._lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._lock_fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            return False
        except Exception:  # noqa: BLE001
            return False

    def release(self) -> None:
        if self._mutex_handle:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:  # noqa: BLE001
                pass
            self._mutex_handle = None
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except Exception:  # noqa: BLE001
                pass
            self._lock_fd = None
        if self._lock_path and os.path.exists(self._lock_path):
            try:
                os.remove(self._lock_path)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 首启配置检测 / 向导
# ---------------------------------------------------------------------------
def _needs_setup(env_path: str) -> bool:
    """.env 缺失或缺少管理员密码（明文/哈希）时返回 True。"""
    if not os.path.exists(env_path):
        return True
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return True
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("ADMIN_PASSWORD_HASH=") or s.startswith("ADMIN_PASSWORD="):
            if s.split("=", 1)[1].strip():
                return False
    return True


def run_setup_wizard(data_dir: str) -> bool:
    """以子进程运行首启向导，返回是否配置完成。"""
    py = runtime_python()
    wizard = setup_wizard_path()
    if not os.path.exists(wizard):
        log("[错误] 未找到首启向导：%s" % wizard)
        return False
    env = os.environ.copy()
    env["RD_DATA_DIR"] = data_dir
    try:
        rc = subprocess.call(
            [py, wizard, "--data-dir", data_dir],
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return rc == 0
    except Exception as e:  # noqa: BLE001
        log("[错误] 首启向导启动失败：%s" % e)
        return False


# ---------------------------------------------------------------------------
# 子进程（后端 / 前端）管理
# ---------------------------------------------------------------------------
class ServiceManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.log_dir = os.path.join(data_dir, "logs")
        self.procs = []

    def _env(self):
        env = os.environ.copy()
        env["RD_DATA_DIR"] = self.data_dir
        env["STREAMLIT_SERVER_HEADLESS"] = "true"
        env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    @staticmethod
    def _creation_flags():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt":
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return flags

    def _open_log(self, name: str):
        os.makedirs(self.log_dir, exist_ok=True)
        return open(os.path.join(self.log_dir, name), "a", encoding="utf-8", buffering=1)

    def _spawn(self, cmd, log_name):
        log_f = self._open_log(log_name)
        proc = subprocess.Popen(
            cmd,
            cwd=app_dir(),
            env=self._env(),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=self._creation_flags(),
        )
        self.procs.append((proc, log_f))
        return proc

    def start_backend(self):
        py = runtime_python()
        cmd = [py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)]
        log("[启动] 后端：%s" % " ".join(cmd))
        return self._spawn(cmd, "backend.log")

    def start_frontend(self):
        py = runtime_python()
        cmd = [
            py, "-m", "streamlit", "run", "app.py",
            "--server.port", str(FRONTEND_PORT),
            "--server.address", "127.0.0.1",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ]
        log("[启动] 前端：%s" % " ".join(cmd))
        return self._spawn(cmd, "frontend.log")

    def stop_all(self):
        for proc, log_f in reversed(self.procs):
            try:
                self._kill_tree(proc)
            except Exception:  # noqa: BLE001
                pass
            try:
                log_f.close()
            except Exception:  # noqa: BLE001
                pass
        self.procs = []

    @staticmethod
    def _kill_tree(proc):
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()


# ---------------------------------------------------------------------------
# 端口就绪探测
# ---------------------------------------------------------------------------
def _port_ready(port: int, timeout: float = 60.0) -> bool:
    url = "http://127.0.0.1:%d/" % port
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as _resp:
                return True
        except urllib.error.HTTPError:
            # 收到 4xx/5xx 也说明服务已在监听
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# 托盘图标
# ---------------------------------------------------------------------------
def _make_image():
    img = Image.new("RGB", (64, 64), (31, 95, 168))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=10, fill=(37, 99, 235),
                        outline=(255, 255, 255), width=2)
    d.text((15, 20), "RD", fill=(255, 255, 255))
    return img


def build_tray(on_open, on_stop, on_quit):
    icon = pystray.Icon(
        "rd_platform",
        _make_image(),
        APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("打开界面", lambda icon, item: on_open(), default=True),
            pystray.MenuItem("停止服务", lambda icon, item: on_stop()),
            pystray.MenuItem("退出", lambda icon, item: on_quit()),
        ),
    )
    return icon


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    single = SingleInstance()
    if not single.acquired:
        log("[提示] 已有实例在运行，本实例退出。")
        return 0

    data_dir = resolve_data_dir()
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as e:
        log("[错误] 无法创建数据目录 %s：%s" % (data_dir, e))
        single.release()
        return 1

    env_path = os.path.join(data_dir, ".env")
    if _needs_setup(env_path):
        log("[配置] 缺少管理员密码配置，弹出首启向导。")
        if not run_setup_wizard(data_dir):
            log("[提示] 首启向导未完成，退出启动器。")
            single.release()
            return 0

    mgr = ServiceManager(data_dir)
    state = {"services_running": False}

    def start_services():
        try:
            mgr.start_backend()
            mgr.start_frontend()
            backend_ok = _port_ready(BACKEND_PORT)
            frontend_ok = _port_ready(FRONTEND_PORT)
            if backend_ok and frontend_ok:
                state["services_running"] = True
                log("[就绪] 后端与前端均已启动，打开浏览器。")
                webbrowser.open(FRONTEND_URL)
            else:
                log("[警告] 服务就绪探测超时（backend=%s, frontend=%s）。" % (backend_ok, frontend_ok))
        except Exception as e:  # noqa: BLE001
            log("[错误] 启动服务失败：%s" % e)

    def on_open():
        webbrowser.open(FRONTEND_URL)

    def on_stop():
        log("[操作] 用户点击「停止服务」。")
        mgr.stop_all()
        state["services_running"] = False

    def on_quit():
        log("[操作] 用户点击「退出」。")
        mgr.stop_all()
        state["services_running"] = False
        icon.stop()

    icon = build_tray(on_open, on_stop, on_quit)
    log("[启动] 开始拉起服务...")
    threading.Thread(target=start_services, daemon=True).start()
    icon.run()
    single.release()
    log("[退出] 启动器已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
