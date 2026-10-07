# -*- coding: utf-8 -*-
"""审计日志 + 自动加密备份 + 归档 + 后台维护循环（从 main.py 抽离）。"""
import os
import json
import glob
import time
import zipfile
import threading
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from config import BASE_DIR, ENV, UPLOAD_DIR, logger, _append_env
from database import SessionLocal
from models import AuditLog


# ============ 审计日志 ============
def _audit(username: str, action: str, detail: str = "", ip: str = ""):
    """写一条审计日志。失败不阻断主流程。"""
    try:
        db = SessionLocal()
        try:
            db.add(AuditLog(username=username, action=action, detail=detail or None, ip=ip or None))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[审计] 写入日志失败：{e}")


# ============ 自动备份（加密） ============
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
try:
    BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", ENV.get("BACKUP_KEEP", "10")) or 10)
except ValueError:
    BACKUP_KEEP = 10
try:
    BACKUP_INTERVAL_HOURS = float(os.getenv("BACKUP_INTERVAL_HOURS", ENV.get("BACKUP_INTERVAL_HOURS", "24")) or 24)
except ValueError:
    BACKUP_INTERVAL_HOURS = 24

# 备份加密密钥（Fernet，首次自动生成并写 .env；丢失将无法解密历史备份）
BACKUP_KEY_RAW = os.getenv("BACKUP_KEY", ENV.get("BACKUP_KEY", "")).strip()
if not BACKUP_KEY_RAW:
    BACKUP_KEY_RAW = Fernet.generate_key().decode()
    _append_env("BACKUP_KEY", BACKUP_KEY_RAW)
    logger.info("[备份] 已自动生成 BACKUP_KEY 并写入 .env")
_fernet = Fernet(BACKUP_KEY_RAW.encode())


# ============ 审计日志归档 ============
AUDIT_ARCHIVE_DIR = os.path.join(BASE_DIR, "audit_archive")
os.makedirs(AUDIT_ARCHIVE_DIR, exist_ok=True)
try:
    AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", ENV.get("AUDIT_RETENTION_DAYS", "90")) or 90)
except ValueError:
    AUDIT_RETENTION_DAYS = 90


def _cleanup_backups():
    """只保留最近 BACKUP_KEEP 份加密备份，删掉更早的。"""
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.zip.enc")))
    while len(files) > BACKUP_KEEP:
        old = files.pop(0)
        try:
            os.remove(old)
            logger.info(f"[备份] 清理过期备份：{os.path.basename(old)}")
        except Exception as e:
            logger.warning(f"[备份] 清理失败：{e}")


def _do_backup() -> str:
    """把数据库 + 文件目录打包成 zip 后 Fernet 加密，返回加密备份文件路径（.zip.enc）。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    enc_path = os.path.join(BACKUP_DIR, f"backup_{ts}.zip.enc")
    tmp_zip = os.path.join(BACKUP_DIR, f".tmp_{ts}.zip")
    db_path = os.path.join(BASE_DIR, "rd_platform.db")

    def _add_dir(zf, root_dir, arc_prefix):
        if not os.path.isdir(root_dir):
            return
        for root, _dirs, files in os.walk(root_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                if os.path.abspath(fp) == os.path.abspath(tmp_zip):
                    continue
                arc = os.path.join(arc_prefix, os.path.relpath(fp, root_dir))
                try:
                    zf.write(fp, arcname=arc)
                except Exception as e:
                    logger.warning(f"[备份] 跳过文件 {fp}：{e}")

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(db_path):
                zf.write(db_path, arcname="rd_platform.db")
            _add_dir(zf, UPLOAD_DIR, "files")
        with open(tmp_zip, "rb") as f:
            plain = f.read()
        with open(enc_path, "wb") as f:
            f.write(_fernet.encrypt(plain))
    finally:
        if os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except Exception:
                pass
    _cleanup_backups()
    logger.info(f"[备份] 已生成加密备份：{enc_path}")
    return enc_path


def _archive_old_audit(retention_days: int = None) -> int:
    """把超过保留期(retention_days)的审计日志归档到 JSONL 文件并从数据库删除，返回归档条数。"""
    if retention_days is None:
        retention_days = AUDIT_RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        old = db.query(AuditLog).filter(AuditLog.ts < cutoff).all()
        if not old:
            return 0
        by_day = {}
        for l in old:
            d = l.ts.strftime("%Y-%m-%d") if l.ts else "unknown"
            by_day.setdefault(d, []).append({
                "ts": l.ts.strftime("%Y-%m-%d %H:%M:%S") if l.ts else "",
                "username": l.username,
                "action": l.action,
                "detail": l.detail,
                "ip": l.ip,
            })
        for d, items in by_day.items():
            fp = os.path.join(AUDIT_ARCHIVE_DIR, f"audit_{d}.jsonl")
            with open(fp, "a", encoding="utf-8") as f:
                for it in items:
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
        for l in old:
            db.delete(l)
        db.commit()
        logger.info(f"[审计] 已归档清理 {len(old)} 条旧日志（> {retention_days} 天）")
        return len(old)
    finally:
        db.close()


def _maintenance_loop():
    """后台维护线程：首次启动 60 秒后执行，之后每 BACKUP_INTERVAL_HOURS 小时备份一次并归档旧审计日志。"""
    time.sleep(60)
    while True:
        try:
            _do_backup()
        except Exception as e:
            logger.error(f"[备份] 定时备份失败：{e}")
        try:
            _archive_old_audit()
        except Exception as e:
            logger.error(f"[审计] 定时归档失败：{e}")
        time.sleep(BACKUP_INTERVAL_HOURS * 3600)


def start_maintenance_thread():
    """启动后台维护线程（备份 + 审计归档）。幂等：重复调用不会重复启动。"""
    if getattr(start_maintenance_thread, "_started", False):
        return
    t = threading.Thread(target=_maintenance_loop, name="maintenance", daemon=True)
    t.start()
    start_maintenance_thread._started = True
