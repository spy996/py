"""认证与安全：密码哈希 + token 签发/校验 + 认证配置。"""
import os
import json
import time
import base64
import hashlib
import hmac
import secrets

from config import ENV, BASE_DIR, logger, _append_env, _remove_env


def _pbkdf2_hash(password: str, salt: str = None) -> str:
    """PBKDF2-SHA256 密码哈希，格式 pbkdf2_sha256$迭代$盐hex$哈希hex。"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return f"pbkdf2_sha256$200000${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), h)
    except Exception:
        return False


# 管理员账号
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", ENV.get("ADMIN_USERNAME", "admin")).strip() or "admin"

# token 签名密钥（首次自动生成并写入 .env）
TOKEN_SECRET = os.getenv("TOKEN_SECRET", ENV.get("TOKEN_SECRET", "")).strip()
if not TOKEN_SECRET:
    TOKEN_SECRET = secrets.token_hex(32)
    _append_env("TOKEN_SECRET", TOKEN_SECRET)
    logger.info("[认证] 已自动生成 TOKEN_SECRET 并写入 .env")

# 密码：优先用哈希；若 .env 只有明文 ADMIN_PASSWORD，则自动哈希并替换
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", ENV.get("ADMIN_PASSWORD_HASH", "")).strip()
if not ADMIN_PASSWORD_HASH:
    plain = os.getenv("ADMIN_PASSWORD", ENV.get("ADMIN_PASSWORD", "")).strip()
    if plain:
        ADMIN_PASSWORD_HASH = _pbkdf2_hash(plain)
        _append_env("ADMIN_PASSWORD_HASH", ADMIN_PASSWORD_HASH)
        _remove_env("ADMIN_PASSWORD")
        logger.info("[认证] 已把 ADMIN_PASSWORD 明文自动哈希为 ADMIN_PASSWORD_HASH 并写入 .env")


# 内部服务 token：QQ 机器人访问后端时携带，走 X-Bot-Token 头（首次自动生成）
BOT_TOKEN = os.getenv("BOT_TOKEN", ENV.get("BOT_TOKEN", "")).strip()
if not BOT_TOKEN:
    BOT_TOKEN = secrets.token_hex(32)
    _append_env("BOT_TOKEN", BOT_TOKEN)
    logger.info("[认证] 已自动生成 BOT_TOKEN 并写入 .env")


def _pwd_version() -> str:
    """密码版本号：当前密码哈希的 SHA256 摘要前 8 位，改密后变化 → 旧 token 立即失效。"""
    if not ADMIN_PASSWORD_HASH:
        return ""
    return hashlib.sha256(ADMIN_PASSWORD_HASH.encode("utf-8")).hexdigest()[:8]


def _make_token(username: str, expires_hours: int = 24 * 7) -> str:
    """HMAC 签名 token：payload{u,v,exp} + 签名。无状态，重启后仍有效。"""
    payload = json.dumps(
        {"u": username, "v": _pwd_version(), "exp": int(time.time()) + expires_hours * 3600},
        separators=(",", ":"),
    )
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode().rstrip("=")
    sig = hmac.new(TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_token(token: str):
    """校验 token，返回用户名；无效/过期/密码已变更返回 None。"""
    try:
        payload_b64, sig = token.split(".")
        expected = hmac.new(TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        pad = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad).decode("utf-8"))
        if int(payload.get("exp", 0)) < time.time():
            return None
        if payload.get("v") != _pwd_version():
            return None
        return payload.get("u")
    except Exception:
        return None


def update_password_hash(new_hash: str) -> None:
    """更新管理员密码哈希（写 .env + 更新内存），改密后旧 token 立即失效。"""
    global ADMIN_PASSWORD_HASH
    ADMIN_PASSWORD_HASH = new_hash
    _append_env("ADMIN_PASSWORD_HASH", new_hash)
