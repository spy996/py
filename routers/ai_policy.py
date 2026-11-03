# -*- coding: utf-8 -*-
"""AI 合规策略与审计接口。"""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import AIAudit, SystemConfig

router = APIRouter(prefix="/ai", tags=["AI合规"])

DEFAULTS = {
    "ai_enabled": "1",
    "ai_mode": "balanced",
    "ai_l3_auto": "1",
    "ai_redact": "1",
    "ai_max_chars": "50000",
}


@router.get("/policy", summary="读取 AI 合规策略")
def get_ai_policy(db: Session = Depends(get_db)):
    rows = {r.key: r.value for r in db.query(SystemConfig).filter(SystemConfig.key.like("ai_%")).all()}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in rows.items() if v is not None})
    return out


@router.put("/policy", summary="更新 AI 合规策略")
def update_ai_policy(payload: dict = Body(...), db: Session = Depends(get_db)):
    allowed = set(DEFAULTS.keys())
    updated = {}
    for k, v in (payload or {}).items():
        if k not in allowed:
            continue
        if k in ("ai_enabled", "ai_l3_auto", "ai_redact"):
            v = "1" if str(v).lower() in ("1", "true", "yes", "on") else "0"
        elif k == "ai_mode":
            v = "strict" if str(v) == "strict" else "balanced"
        elif k == "ai_max_chars":
            try:
                v = str(max(1000, min(200000, int(float(v)))))
            except Exception:
                raise HTTPException(400, "字符上限必须是数字")
        row = db.query(SystemConfig).filter(SystemConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(SystemConfig(key=k, value=v))
        updated[k] = v
    db.commit()
    return {"ok": True, "updated": updated}


@router.get("/audit", summary="AI 外发审计（不返回正文）")
def ai_audit(limit: int = 50, db: Session = Depends(get_db)):
    items = db.query(AIAudit).order_by(AIAudit.id.desc()).limit(max(1, min(limit, 500))).all()
    return [{
        "id": a.id,
        "ts": a.ts.strftime("%Y-%m-%d %H:%M:%S") if a.ts else "",
        "username": a.username or "",
        "module": a.module or "",
        "ref_type": a.ref_type or "",
        "ref_id": a.ref_id,
        "sensitivity": a.sensitivity or "",
        "chars": a.chars or 0,
        "action": a.action or "",
        "reason": a.reason or "",
        "model": a.model or "",
    } for a in items]
