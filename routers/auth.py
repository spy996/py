# -*- coding: utf-8 -*-
"""认证（登录/续期/改密）+ 审计日志 + 加密备份 路由。"""
import os
import re
import glob
import time
from datetime import datetime

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

import security
from config import logger
from database import get_db
from models import AuditLog
from utils.audit_backup import (
    _audit, _do_backup, _archive_old_audit,
    AUDIT_ARCHIVE_DIR, AUDIT_RETENTION_DAYS,
    BACKUP_DIR, BACKUP_KEEP, _fernet,
)

router = APIRouter(tags=["认证与系统"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str


@router.post("/auth/login", summary="登录获取访问 token")
def login(req: LoginRequest):
    if not security.ADMIN_PASSWORD_HASH:
        _audit(req.username.strip(), "POST /auth/login", "失败：未配置管理员密码")
        raise HTTPException(status_code=500, detail="未配置管理员密码，请在 .env 设置 ADMIN_PASSWORD")
    if req.username.strip() != security.ADMIN_USERNAME or not security._verify_password(req.password, security.ADMIN_PASSWORD_HASH):
        _audit(req.username.strip(), "POST /auth/login", "失败：用户名或密码错误")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = security._make_token(req.username.strip())
    _audit(req.username.strip(), "POST /auth/login", "登录成功")
    return {"token": token, "username": req.username.strip(), "expires_at": int(time.time()) + 7 * 24 * 3600}


@router.post("/auth/refresh", summary="用有效 token 换取新 token（续期）")
def refresh_token(request: Request):
    username = getattr(request.state, "username", None)
    if not username or username == "bot":
        raise HTTPException(status_code=401, detail="无法刷新 token")
    token = security._make_token(username)
    _audit(username, "POST /auth/refresh", "token 续期")
    return {"token": token, "username": username, "expires_at": int(time.time()) + 7 * 24 * 3600}


@router.post("/auth/change-password", summary="修改管理员密码（改后旧 token 立即失效）")
def change_password(req: ChangePasswordRequest):
    if not security.ADMIN_PASSWORD_HASH:
        raise HTTPException(status_code=500, detail="未配置管理员密码")
    if req.username.strip() != security.ADMIN_USERNAME or not security._verify_password(req.old_password, security.ADMIN_PASSWORD_HASH):
        _audit(req.username.strip(), "POST /auth/change-password", "失败：旧密码错误")
        raise HTTPException(status_code=401, detail="用户名或旧密码错误")
    new_pwd = (req.new_password or "").strip()
    if len(new_pwd) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    security.update_password_hash(security._pbkdf2_hash(new_pwd))
    _audit(req.username.strip(), "POST /auth/change-password", "修改密码成功")
    logger.info("[认证] 管理员密码已修改，旧 token 已失效")
    return {"message": "密码已修改，请使用新密码重新登录"}


@router.get("/audit/logs", summary="查询审计日志")
def audit_logs(limit: int = 200, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 2000))
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "ts": l.ts.strftime("%Y-%m-%d %H:%M:%S") if l.ts else "",
            "username": l.username,
            "action": l.action,
            "detail": l.detail,
            "ip": l.ip,
        }
        for l in logs
    ]


@router.post("/audit/archive", summary="归档清理过期审计日志")
def audit_archive(days: int = None):
    n = _archive_old_audit(days)
    _audit("admin", "POST /audit/archive", f"归档清理 {n} 条旧日志")
    return {"archived": n, "archive_dir": AUDIT_ARCHIVE_DIR, "retention_days": days or AUDIT_RETENTION_DAYS}


@router.get("/audit/archive/list", summary="列出已归档的审计日志文件")
def audit_archive_list():
    files = sorted(glob.glob(os.path.join(AUDIT_ARCHIVE_DIR, "audit_*.jsonl")), reverse=True)
    items = []
    for fp in files:
        try:
            size = os.path.getsize(fp)
            mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            size, mtime = 0, ""
        items.append({"name": os.path.basename(fp), "size": size, "created_at": mtime})
    return {"count": len(items), "items": items, "dir": AUDIT_ARCHIVE_DIR}


@router.post("/backup/create", summary="手动触发一次备份")
def backup_create():
    try:
        zip_path = _do_backup()
        _audit("admin", "POST /backup/create", f"备份成功：{os.path.basename(zip_path)}")
        return {"message": "备份完成", "file": os.path.basename(zip_path), "path": zip_path}
    except Exception as e:
        logger.error(f"[备份] 手动备份失败：{e}")
        raise HTTPException(status_code=500, detail=f"备份失败：{e}")


@router.get("/backup/list", summary="列出加密备份文件")
def backup_list():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.zip.enc")), reverse=True)
    items = []
    for fp in files:
        try:
            size = os.path.getsize(fp)
            mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            size, mtime = 0, ""
        items.append({"name": os.path.basename(fp), "size": size, "created_at": mtime, "path": fp})
    return {"count": len(items), "items": items, "dir": BACKUP_DIR, "keep": BACKUP_KEEP, "encrypted": True}


@router.get("/backup/download", summary="解密并下载指定备份文件")
def backup_download(name: str):
    # 防目录穿越：只允许 backup_*.zip.enc
    if not re.fullmatch(r"backup_\d{8}_\d{6}\.zip\.enc", name or ""):
        raise HTTPException(status_code=400, detail="非法的备份文件名")
    fp = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="备份文件不存在")
    try:
        with open(fp, "rb") as f:
            enc = f.read()
        plain = _fernet.decrypt(enc)
    except Exception as e:
        logger.error(f"[备份] 解密失败 {name}：{e}")
        raise HTTPException(status_code=500, detail="备份解密失败（密钥可能已变更）")
    return Response(content=plain, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name[:-4]}"'
    })
