"""全局配置：环境变量 + .env 加载 + 路径 + DeepSeek 配置 + 日志。"""
import os
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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


ENV = _load_env(os.path.join(BASE_DIR, ".env"))

# 存储路径：文件本体 + 提取文本（默认项目目录下 data/，可在 .env 里用 UPLOAD_DIR 覆盖为任意绝对路径，便于跨电脑迁移）
UPLOAD_DIR = os.getenv("UPLOAD_DIR", ENV.get("UPLOAD_DIR", os.path.join(BASE_DIR, "data")))
EXTRACT_DIR = os.path.join(UPLOAD_DIR, "extracted")
os.makedirs(EXTRACT_DIR, exist_ok=True)


DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'rd_platform.db')}"

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
