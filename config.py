"""全局配置：环境变量 + .env 加载 + 路径 + DeepSeek 配置 + 日志。"""
import os
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_data_dir() -> str:
    """解析数据目录（数据库 / .env / 上传文件 / 备份 / 聊天记录 等持久化数据的根目录）。

    优先级：
      1) 显式环境变量 RD_DATA_DIR（便携 U 盘 / 自定义数据位置）
      2) 打包态：存在 BASE_DIR/_packaged 标记时，用 %LOCALAPPDATA%\\RDPlatform\\data
         （卸载不丢数据、Program Files 不可写）
      3) 开发 / 旧部署：回退 BASE_DIR（保持现状）
    """
    if os.getenv("RD_DATA_DIR"):
        return os.getenv("RD_DATA_DIR")
    if os.path.exists(os.path.join(BASE_DIR, "_packaged")):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "RDPlatform", "data")
    return BASE_DIR


DATA_DIR = _resolve_data_dir()
ENV_PATH = os.path.join(DATA_DIR, ".env")
# 向后兼容：仅在纯开发态（DATA_DIR==BASE_DIR，即未设置 RD_DATA_DIR、无 _packaged 标记）
# 才允许 .env 缺失时回退读 BASE_DIR/.env。打包态 / 显式 RD_DATA_DIR 时，.env 必须严格
# 落在 DATA_DIR 下，避免误读开发密钥或旧配置。
_IS_DEV = (DATA_DIR == BASE_DIR and not os.path.exists(os.path.join(BASE_DIR, "_packaged")))
if _IS_DEV and not os.path.exists(ENV_PATH) and os.path.exists(os.path.join(BASE_DIR, ".env")):
    ENV_PATH = os.path.join(BASE_DIR, ".env")
os.makedirs(DATA_DIR, exist_ok=True)


def _load_env(path: str) -> dict:
    """轻量 .env 加载：KEY=VALUE，忽略注释和空行（避免额外依赖 python-dotenv）"""
    env = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env(ENV_PATH)

# 存储路径：文件本体 + 提取文本（默认数据目录下 uploads/，可在 .env 里用 UPLOAD_DIR 覆盖为任意绝对路径，便于跨电脑迁移）
UPLOAD_DIR = os.getenv("UPLOAD_DIR", ENV.get("UPLOAD_DIR", os.path.join(DATA_DIR, "uploads")))
EXTRACT_DIR = os.path.join(UPLOAD_DIR, "extracted")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXTRACT_DIR, exist_ok=True)


DB_PATH = os.path.join(DATA_DIR, "rd_platform.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# ============ DeepSeek 智能问答配置 ============
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", ENV.get("DEEPSEEK_API_KEY", ""))
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", ENV.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"))

# ============ 日志 ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rd_platform")


def _append_env(key: str, value: str) -> None:
    """向 .env 追加/更新一个键值。"""
    env_path = ENV_PATH
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _remove_env(key: str) -> None:
    """从 .env 删除某个键（用于把明文密码换成哈希）。"""
    env_path = ENV_PATH
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    kept = [ln for ln in lines if not ln.strip().startswith(key + "=")]
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(kept)
