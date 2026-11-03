# -*- coding: utf-8 -*-
"""网络通讯台账路由：站点 / 定级系统 / 设备 / IP-MAC / 拓扑图 / 等保资料 / 整改项。

设计原则：
- 不引入 AI，解析全部走 utils/net_rules.py 的确定性规则；
- 所有写操作记 net_changes；批量导入记 net_import_batches（可回滚）；
- 文件统一落 UPLOAD_DIR/net/，随现有加密备份一起备份。
"""
from __future__ import annotations

import io
import json
import os
import re
import uuid
from datetime import datetime, date, timedelta
from typing import List, Optional
from urllib.parse import quote

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from config import UPLOAD_DIR, logger
from database import SessionLocal, get_db
from models import (
    NetSite, NetSystem, NetDevice, NetAddress, NetTopology, NetReport,
    NetFinding, NetChange, NetImportBatch, NetDict,
)
from utils import net_rules as R
from utils import net_forms

router = APIRouter(prefix="/net", tags=["网络通讯台账"])

NET_ROOT = os.path.join(UPLOAD_DIR, "net")
REPORT_DIR = os.path.join(NET_ROOT, "reports")
TOPO_DIR = os.path.join(NET_ROOT, "topology")
TMP_DIR = os.path.join(NET_ROOT, "tmp")
for _d in (NET_ROOT, REPORT_DIR, TOPO_DIR, TMP_DIR):
    os.makedirs(_d, exist_ok=True)

DEVICE_FIELDS = [
    "site_id", "system_id", "category", "name", "brand_model", "is_virtual", "os_version",
    "purpose", "importance", "zone", "room", "mgmt_ip", "mac", "is_hot_standby", "quantity",
    "biz_app", "db_version", "middleware", "antivirus", "asset_no", "sn", "owner", "rack",
    "warranty_end", "status", "remark", "last_check_at",
]
ADDR_FIELDS = [
    "ip", "mac", "mac_raw", "department", "person", "device_type", "location",
    "site_id", "status", "wangjian", "remark",
]
REPORT_FIELDS = [
    "system_id", "site_id", "doc_type", "report_year", "project_no", "report_no",
    "level", "level_s", "level_a", "agency", "eval_start", "eval_end", "report_date",
    "conclusion", "pass_rate", "item_total", "item_pass", "item_partial", "item_fail",
    "item_na", "problem_count", "major_risk", "last_eval_date", "last_score",
    "last_conclusion", "next_due", "file_role", "remark", "confirmed",
]


# ---------------------------------------------------------------- 通用工具
def _op(request: Request) -> str:
    if request is None:
        return "admin"
    return getattr(request.state, "username", "") or "admin"


def _dir(*parts: str) -> str:
    p = os.path.join(NET_ROOT, *parts)
    os.makedirs(p, exist_ok=True)
    return p


def _save_file(file: UploadFile, folder: str) -> tuple[str, str, int]:
    """保存上传文件，返回 (原文件名, 磁盘路径, 大小)。"""
    original = file.filename or "未命名"
    ext = os.path.splitext(original)[1].lower() or ".bin"
    stored = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    path = os.path.join(folder, stored)
    data = file.file.read()
    with open(path, "wb") as f:
        f.write(data)
    return original, path, len(data)


def _clean(v, n=200):
    if v is None:
        return None
    s = str(v).strip()
    return s[:n] if s else None


def _to_int(v):
    try:
        return int(float(v))
    except Exception:
        return None


def _to_float(v):
    try:
        return float(v)
    except Exception:
        return None


def _to_date(v):
    if not v:
        return None
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", str(v))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})[-/.年](\d{1,2})", str(v))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return None


def _log(db: Session, entity: str, eid: Optional[int], label: str, field: str,
         old, new, operator: str, reason: str = ""):
    if str(old or "") == str(new or ""):
        return
    db.add(NetChange(entity_type=entity, entity_id=eid, entity_label=_clean(label, 200),
                     field=field, old_value=None if old is None else str(old)[:2000],
                     new_value=None if new is None else str(new)[:2000],
                     reason=reason or None, operator=operator))


def _next_eval(eval_end: str, level: str) -> str:
    """下次测评日：三级 +1 年、二级 +2 年（默认规则，可在系统中改）。"""
    d = _to_date(eval_end)
    if not d:
        return ""
    try:
        y, m = int(d[:4]), int(d[5:7])
        dd = int(d[8:10]) if len(d) >= 10 else 1
        add = 1 if level == "三级" else 2
        return f"{y + add}-{m:02d}-{dd:02d}"
    except Exception:
        return ""


def _match_system(db: Session, text: str):
    """按站点关键词 + 系统名匹配定级系统，返回 (system, score)。"""
    if not text:
        return None, 0
    t = R.norm(text)
    best, best_score = None, 0
    code = R.guess_site_code(t)
    systems = db.query(NetSystem).all()
    for s in systems:
        score = 0
        if R.norm(s.name) and R.norm(s.name) in t:
            score = 100 + len(s.name)
        else:
            site = db.query(NetSite).filter(NetSite.id == s.site_id).first()
            if code and site and site.code == code:
                score = 50 + len(code)
            else:
                # 系统名去掉通用后缀后的关键词
                kw = re.sub(r"(电站|风电场|光伏|开关站)?(计算机监控系统|电力监控系统|监控系统)", "", s.name)
                kw = R.norm(kw)
                if kw and kw in t:
                    score = 30 + len(kw)
        if score > best_score:
            best, best_score = s, score
    return best, best_score


def _xlsx_response(wb, filename: str) -> Response:
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _style_header(ws):
    fill = PatternFill("solid", fgColor="1F5FA8")
    font = Font(color="FFFFFF", bold=True)
    for c in ws[1]:
        c.fill = fill
        c.font = font
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"


# ---------------------------------------------------------------- 字典序列化
def _site_dict(s: NetSite, system_count=0, device_count=0) -> dict:
    return {
        "id": s.id, "code": s.code, "name": s.name, "company": s.company or "",
        "site_type": s.site_type or "", "region": s.region or "", "address": s.address or "",
        "manager": s.manager or "", "phone": s.phone or "", "sort_order": s.sort_order or 0,
        "remark": s.remark or "", "system_count": system_count, "device_count": device_count,
    }


def _system_dict(s: NetSystem, site_name="") -> dict:
    return {
        "id": s.id, "site_id": s.site_id, "site_name": site_name, "name": s.name,
        "level": s.level or "", "level_s": s.level_s or "", "level_a": s.level_a or "",
        "deploy_zone": s.deploy_zone or "", "extension": s.extension or "",
        "filing_no": s.filing_no or "", "filing_date": s.filing_date or "",
        "filing_org": s.filing_org or "", "integrator": s.integrator or "",
        "sys_version": s.sys_version or "", "manager": s.manager or "", "status": s.status or "",
        "last_eval_date": s.last_eval_date or "", "last_conclusion": s.last_conclusion or "",
        "next_eval_date": s.next_eval_date or "", "remark": s.remark or "",
    }


def _device_dict(d: NetDevice, site_name="", system_name="") -> dict:
    return {
        "id": d.id, "site_id": d.site_id, "site_name": site_name, "system_id": d.system_id,
        "system_name": system_name, "category": d.category, "name": d.name,
        "brand_model": d.brand_model or "", "is_virtual": d.is_virtual or "否",
        "os_version": d.os_version or "", "purpose": d.purpose or "",
        "importance": d.importance or "", "zone": d.zone or "", "room": d.room or "",
        "mgmt_ip": d.mgmt_ip or "", "mac": d.mac or "", "is_hot_standby": d.is_hot_standby or "否",
        "quantity": d.quantity or 1, "biz_app": d.biz_app or "", "db_version": d.db_version or "",
        "middleware": d.middleware or "", "antivirus": d.antivirus or "",
        "asset_no": d.asset_no or "", "sn": d.sn or "", "owner": d.owner or "",
        "rack": d.rack or "", "warranty_end": d.warranty_end or "", "status": d.status or "",
        "source": d.source or "", "last_check_at": d.last_check_at or "", "remark": d.remark or "",
        "created_at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "",
        "updated_at": d.updated_at.strftime("%Y-%m-%d %H:%M") if d.updated_at else "",
    }


def _addr_dict(a: NetAddress, site_name="") -> dict:
    return {
        "id": a.id, "ip": a.ip or "", "mac": a.mac or "", "mac_raw": a.mac_raw or "",
        "department": a.department or "", "person": a.person or "",
        "device_type": a.device_type or "", "location": a.location or "",
        "site_id": a.site_id, "site_name": site_name, "status": a.status or "",
        "wangjian": a.wangjian or "", "remark": a.remark or "", "source": a.source or "",
        "first_seen": a.first_seen or "",
        "updated_at": a.updated_at.strftime("%Y-%m-%d %H:%M") if a.updated_at else "",
    }


def _topo_dict(t: NetTopology, system_name="") -> dict:
    return {
        "id": t.id, "site_id": t.site_id, "system_id": t.system_id, "system_name": system_name,
        "name": t.name, "kind": t.kind or "", "version": t.version or "",
        "effective_date": t.effective_date or "", "note": t.note or "",
        "is_current": int(t.is_current or 0), "uploaded_by": t.uploaded_by or "",
        "has_source": bool(t.source_path and os.path.exists(t.source_path)),
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
    }


def _report_dict(r: NetReport, system_name="", site_name="") -> dict:
    return {
        "id": r.id, "system_id": r.system_id, "system_name": system_name, "site_id": r.site_id,
        "site_name": site_name, "doc_type": r.doc_type, "report_year": r.report_year,
        "project_no": r.project_no or "", "report_no": r.report_no or "", "level": r.level or "",
        "level_s": r.level_s or "", "level_a": r.level_a or "", "agency": r.agency or "",
        "eval_start": r.eval_start or "", "eval_end": r.eval_end or "", "report_date": r.report_date or "",
        "conclusion": r.conclusion or "", "pass_rate": r.pass_rate,
        "item_total": r.item_total, "item_pass": r.item_pass, "item_partial": r.item_partial,
        "item_fail": r.item_fail, "item_na": r.item_na, "problem_count": r.problem_count,
        "major_risk": int(r.major_risk or 0), "last_eval_date": r.last_eval_date or "",
        "last_score": r.last_score, "last_conclusion": r.last_conclusion or "",
        "next_due": r.next_due or "", "original_name": r.original_name,
        "file_role": r.file_role or "主件", "size": r.size or 0,
        "confidence": r.confidence or 0, "confirmed": int(r.confirmed or 0),
        "remark": r.remark or "",
        "uploaded_at": r.uploaded_at.strftime("%Y-%m-%d %H:%M") if r.uploaded_at else "",
    }


def _finding_dict(f: NetFinding, system_name="") -> dict:
    return {
        "id": f.id, "report_id": f.report_id, "system_id": f.system_id, "system_name": system_name,
        "problem_no": f.problem_no, "security_class": f.security_class or "",
        "control_point": f.control_point or "", "target": f.target or "",
        "requirement": f.requirement or "", "description": f.description,
        "risk_level": f.risk_level or "", "suggestion": f.suggestion or "",
        "source": f.source or "", "prev_status": f.prev_status or "",
        "owner": f.owner or "", "plan_date": f.plan_date or "", "done_date": f.done_date or "",
        "status": f.status or "", "remark": f.remark or "",
        "created_at": f.created_at.strftime("%Y-%m-%d %H:%M") if f.created_at else "",
    }


# ---------------------------------------------------------------- 初始化数据
SEED_SITES = [
    ("JKS", "集控中心", "木里河公司", "集控中心", "成都青羊区华电国际大楼11F", 1),
    ("STB", "上通坝电站", "木里河公司", "水电站", "木里河上通坝", 2),
    ("KJW", "卡基娃电站", "木里河公司", "水电站", "木里河卡基娃", 3),
    ("EGB", "俄公堡电站", "木里河公司", "水电站", "木里河俄公堡", 4),
    ("LZ", "立洲电站", "木里河公司", "水电站", "木里河立洲", 5),
    ("DF", "大丰风电场", "唐央公司", "风电场", "木里唐央大丰", 6),
    ("RD", "绒达光伏电站", "唐央公司", "光伏电站", "木里唐央绒达", 7),
    ("DY", "当依光伏电站", "木里新能", "光伏电站", "木里当依", 8),
    ("HK", "汇康开关站", "木里新能", "开关站", "木里汇康", 9),
    ("RB", "让白光伏电站（杜依）", "木里新能", "光伏电站", "木里让白/杜依", 10),
]
SEED_SYSTEMS = [
    ("JKS", "集控中心计算机监控系统", "S2", "A3", "三级", "51000026011-19001", "安全区Ⅰ/Ⅱ"),
    ("STB", "上通坝电站计算机监控系统", "S2", "A2", "二级", "51342226672-19001", "安全区Ⅰ"),
    ("KJW", "卡基娃电站计算机监控系统", "S2", "A2", "二级", "51342226672-22004", "安全区Ⅰ"),
    ("EGB", "俄公堡电站计算机监控系统", "S2", "A2", "二级", "51342226672-22003", "安全区Ⅰ"),
    ("LZ", "立洲电站计算机监控系统", "S2", "A2", "二级", "51342226672-19002", "安全区Ⅰ"),
    ("DF", "大丰风电场电力监控系统", "S2", "A3", "三级", None, "生产控制大区"),
    ("RD", "绒达光伏电站电力监控系统", "S3", "A3", "三级", "51342226004-24001", "生产控制+管理信息大区"),
    ("DY", "220kV当依光伏电站电力监控系统", "S2", "A3", "三级", "51342226003-23001", "生产控制+管理信息大区"),
    ("HK", "220kV汇康开关站电力监控系统", "S2", "A3", "三级", "51342226003-24002", "生产控制大区"),
    ("RB", "让白光伏电站电力监控系统", "S2", "A2", "二级", None, ""),
]


DEFAULT_DEPTS = [
    ("新能源运管中心", "新能源 运管中心"),
    ("卡基娃电站", "卡基娃"),
    ("计划发展部", "计划部"),
    ("工程管理部", "工程部"),
    ("集控中心", "集控"),
]


def _ensure_dicts():
    db = SessionLocal()
    try:
        if db.query(NetDict).count() > 0:
            return
        for name, alias in DEFAULT_DEPTS:
            db.add(NetDict(kind="department", name=name, alias=alias))
        db.commit()
        logger.info("[网络台账] 已初始化部门字典 %d 条", len(DEFAULT_DEPTS))
    except Exception as e:
        db.rollback()
        logger.error("[网络台账] 字典初始化失败：%s", e)
    finally:
        db.close()


def _ensure_seed():
    db = SessionLocal()
    try:
        if db.query(NetSite).count() > 0:
            return
        code_map = {}
        for code, name, company, stype, addr, order in SEED_SITES:
            s = NetSite(code=code, name=name, company=company, site_type=stype,
                        address=addr, sort_order=order)
            db.add(s)
            db.flush()
            code_map[code] = s.id
        for code, name, ls, la, level, filing, zone in SEED_SYSTEMS:
            db.add(NetSystem(site_id=code_map[code], name=name, level_s=ls, level_a=la,
                             level=level, filing_no=filing, deploy_zone=zone,
                             extension="工业控制系统" if "监控" in name else ""))
        db.commit()
        logger.info("[网络台账] 已初始化 %d 个站点、%d 个定级系统", len(SEED_SITES), len(SEED_SYSTEMS))
    except Exception as e:
        db.rollback()
        logger.error("[网络台账] 初始化失败：%s", e)
    finally:
        db.close()


_ensure_seed()
_ensure_dicts()


# ---------------------------------------------------------------- 站点
@router.get("/sites", summary="站点列表")
def list_sites(db: Session = Depends(get_db)):
    sites = db.query(NetSite).order_by(NetSite.sort_order, NetSite.id).all()
    sc = dict(db.query(NetSystem.site_id, func.count(NetSystem.id)).group_by(NetSystem.site_id).all())
    dc = dict(db.query(NetDevice.site_id, func.count(NetDevice.id)).group_by(NetDevice.site_id).all())
    return [_site_dict(s, sc.get(s.id, 0), dc.get(s.id, 0)) for s in sites]


@router.post("/sites", summary="新增站点")
def create_site(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    code = _clean(payload.get("code"), 30)
    name = _clean(payload.get("name"), 100)
    if not code or not name:
        raise HTTPException(400, "站点编码和名称必填")
    if db.query(NetSite).filter(NetSite.code == code).first():
        raise HTTPException(400, "站点编码已存在")
    s = NetSite(code=code, name=name, company=_clean(payload.get("company"), 50),
                site_type=_clean(payload.get("site_type"), 20),
                region=_clean(payload.get("region"), 100), address=_clean(payload.get("address"), 200),
                manager=_clean(payload.get("manager"), 50), phone=_clean(payload.get("phone"), 30),
                sort_order=_to_int(payload.get("sort_order")) or 0,
                remark=_clean(payload.get("remark"), 2000))
    db.add(s)
    db.commit()
    db.refresh(s)
    return _site_dict(s)


@router.put("/sites/{sid}", summary="编辑站点")
def update_site(sid: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    s = db.query(NetSite).get(sid) if hasattr(db, "get") else db.query(NetSite).filter(NetSite.id == sid).first()
    if not s:
        raise HTTPException(404, "站点不存在")
    for k in ("code", "name", "company", "site_type", "region", "address", "manager", "phone", "remark"):
        if k in payload:
            old = getattr(s, k)
            new = _clean(payload[k], 200)
            if new != old:
                _log(db, "site", s.id, s.name, k, old, new, _op(request))
                setattr(s, k, new)
    if "sort_order" in payload:
        s.sort_order = _to_int(payload.get("sort_order")) or 0
    db.commit()
    return _site_dict(s)


@router.delete("/sites/{sid}", summary="删除站点")
def delete_site(sid: int, db: Session = Depends(get_db)):
    s = db.query(NetSite).filter(NetSite.id == sid).first()
    if not s:
        raise HTTPException(404, "站点不存在")
    if db.query(NetSystem).filter(NetSystem.site_id == sid).count():
        raise HTTPException(400, "该站点下还有定级系统，请先删除系统")
    if db.query(NetDevice).filter(NetDevice.site_id == sid).count():
        raise HTTPException(400, "该站点下还有设备，请先删除设备")
    db.delete(s)
    db.commit()
    return {"message": "已删除"}


# ---------------------------------------------------------------- 定级系统
@router.get("/systems", summary="定级系统列表")
def list_systems(site_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(NetSystem)
    if site_id:
        q = q.filter(NetSystem.site_id == site_id)
    items = q.order_by(NetSystem.site_id, NetSystem.id).all()
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    dc = dict(db.query(NetDevice.system_id, func.count(NetDevice.id)).group_by(NetDevice.system_id).all())
    rc = dict(db.query(NetReport.system_id, func.count(NetReport.id)).group_by(NetReport.system_id).all())
    out = []
    for s in items:
        d = _system_dict(s, smap.get(s.site_id, ""))
        d["device_count"] = dc.get(s.id, 0)
        d["report_count"] = rc.get(s.id, 0)
        out.append(d)
    return out


@router.post("/systems", summary="新增定级系统")
def create_system(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    site_id = _to_int(payload.get("site_id"))
    name = _clean(payload.get("name"), 200)
    if not site_id or not name:
        raise HTTPException(400, "所属站点和系统名称必填")
    s = NetSystem(site_id=site_id, name=name,
                  level_s=_clean(payload.get("level_s"), 5), level_a=_clean(payload.get("level_a"), 5),
                  level=_clean(payload.get("level"), 10), deploy_zone=_clean(payload.get("deploy_zone"), 50),
                  extension=_clean(payload.get("extension"), 50), filing_no=_clean(payload.get("filing_no"), 60),
                  filing_date=_to_date(payload.get("filing_date")), filing_org=_clean(payload.get("filing_org"), 100),
                  integrator=_clean(payload.get("integrator"), 120), sys_version=_clean(payload.get("sys_version"), 120),
                  manager=_clean(payload.get("manager"), 50), status=_clean(payload.get("status"), 20) or "在用",
                  next_eval_date=_to_date(payload.get("next_eval_date")),
                  remark=_clean(payload.get("remark"), 2000))
    db.add(s)
    db.commit()
    db.refresh(s)
    return _system_dict(s)


@router.put("/systems/{sid}", summary="编辑定级系统")
def update_system(sid: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    s = db.query(NetSystem).filter(NetSystem.id == sid).first()
    if not s:
        raise HTTPException(404, "系统不存在")
    fields = ["name", "level_s", "level_a", "level", "deploy_zone", "extension", "filing_no",
              "filing_org", "integrator", "sys_version", "manager", "status", "remark"]
    for k in fields:
        if k in payload:
            old = getattr(s, k)
            new = _clean(payload[k], 200)
            if new != old:
                _log(db, "system", s.id, s.name, k, old, new, _op(request))
                setattr(s, k, new)
    for k in ("filing_date", "next_eval_date"):
        if k in payload:
            setattr(s, k, _to_date(payload[k]))
    if "site_id" in payload:
        s.site_id = _to_int(payload["site_id"]) or s.site_id
    db.commit()
    return _system_dict(s)


@router.delete("/systems/{sid}", summary="删除定级系统")
def delete_system(sid: int, db: Session = Depends(get_db)):
    s = db.query(NetSystem).filter(NetSystem.id == sid).first()
    if not s:
        raise HTTPException(404, "系统不存在")
    if db.query(NetDevice).filter(NetDevice.system_id == sid).count():
        raise HTTPException(400, "该系统下还有设备，请先删除设备")
    if db.query(NetReport).filter(NetReport.system_id == sid).count():
        raise HTTPException(400, "该系统下还有等保资料，请先删除资料")
    db.delete(s)
    db.commit()
    return {"message": "已删除"}


# ---------------------------------------------------------------- 设备
@router.get("/devices", summary="设备列表")
def list_devices(site_id: Optional[int] = None, system_id: Optional[int] = None,
                 category: Optional[str] = None, zone: Optional[str] = None,
                 status: Optional[str] = None, q: Optional[str] = None,
                 limit: int = 2000, db: Session = Depends(get_db)):
    query = db.query(NetDevice)
    if site_id:
        query = query.filter(NetDevice.site_id == site_id)
    if system_id:
        query = query.filter(NetDevice.system_id == system_id)
    if category:
        query = query.filter(NetDevice.category == category)
    if zone:
        query = query.filter(NetDevice.zone == zone)
    if status:
        query = query.filter(NetDevice.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(NetDevice.name.like(like), NetDevice.brand_model.like(like),
                                 NetDevice.mgmt_ip.like(like), NetDevice.mac.like(like),
                                 NetDevice.remark.like(like)))
    total = query.count()
    items = query.order_by(NetDevice.site_id, NetDevice.system_id, NetDevice.category, NetDevice.id).limit(limit).all()
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    return {"total": total,
            "items": [_device_dict(d, smap.get(d.site_id, ""), sysmap.get(d.system_id, "")) for d in items]}


@router.post("/devices", summary="新增设备")
def create_device(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    site_id = _to_int(payload.get("site_id"))
    category = _clean(payload.get("category"), 20)
    name = _clean(payload.get("name"), 200)
    if not site_id or not category or not name:
        raise HTTPException(400, "站点、设备类别、设备名称必填")
    d = NetDevice(site_id=site_id, category=category, name=name,
                  system_id=_to_int(payload.get("system_id")),
                  brand_model=_clean(payload.get("brand_model"), 200),
                  is_virtual=_clean(payload.get("is_virtual"), 5) or "否",
                  os_version=_clean(payload.get("os_version"), 120),
                  purpose=_clean(payload.get("purpose"), 200),
                  importance=_clean(payload.get("importance"), 10) or "重要",
                  zone=_clean(payload.get("zone"), 30), room=_clean(payload.get("room"), 100),
                  mgmt_ip=_clean(payload.get("mgmt_ip"), 120), mac=_clean(payload.get("mac"), 30),
                  is_hot_standby=_clean(payload.get("is_hot_standby"), 5) or "否",
                  quantity=_to_int(payload.get("quantity")) or 1,
                  biz_app=_clean(payload.get("biz_app"), 200),
                  db_version=_clean(payload.get("db_version"), 120),
                  middleware=_clean(payload.get("middleware"), 120),
                  antivirus=_clean(payload.get("antivirus"), 120),
                  asset_no=_clean(payload.get("asset_no"), 60), sn=_clean(payload.get("sn"), 100),
                  owner=_clean(payload.get("owner"), 50), rack=_clean(payload.get("rack"), 50),
                  warranty_end=_to_date(payload.get("warranty_end")),
                  status=_clean(payload.get("status"), 20) or "在用",
                  remark=_clean(payload.get("remark"), 2000), source="manual",
                  updated_at=datetime.utcnow())
    db.add(d)
    db.commit()
    db.refresh(d)
    return _device_dict(d)


@router.put("/devices/{did}", summary="编辑设备")
def update_device(did: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    d = db.query(NetDevice).filter(NetDevice.id == did).first()
    if not d:
        raise HTTPException(404, "设备不存在")
    for k in DEVICE_FIELDS:
        if k in payload:
            if k in ("site_id", "system_id", "quantity"):
                new = _to_int(payload[k])
            elif k == "warranty_end":
                new = _to_date(payload[k])
            else:
                new = _clean(payload[k], 2000)
            old = getattr(d, k)
            if str(old or "") != str(new or ""):
                _log(db, "device", d.id, d.name, k, old, new, _op(request))
                setattr(d, k, new)
    d.updated_at = datetime.utcnow()
    db.commit()
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    return _device_dict(d, smap.get(d.site_id, ""), sysmap.get(d.system_id, ""))


@router.delete("/devices/{did}", summary="删除设备")
def delete_device(did: int, db: Session = Depends(get_db)):
    d = db.query(NetDevice).filter(NetDevice.id == did).first()
    if not d:
        raise HTTPException(404, "设备不存在")
    db.delete(d)
    db.commit()
    return {"message": "已删除"}


@router.post("/devices/batch-update", summary="批量修改设备字段")
def batch_update_devices(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    ids = payload.get("ids") or []
    field = payload.get("field")
    value = payload.get("value")
    allowed = {"system_id", "zone", "importance", "status", "owner", "room", "category"}
    if field not in allowed:
        raise HTTPException(400, f"不支持批量修改字段：{field}")
    n = 0
    for d in db.query(NetDevice).filter(NetDevice.id.in_(ids)).all():
        old = getattr(d, field)
        new = _to_int(value) if field in ("system_id",) else _clean(value, 200)
        if str(old or "") != str(new or ""):
            _log(db, "device", d.id, d.name, field, old, new, _op(request), "批量修改")
            setattr(d, field, new)
            n += 1
    db.commit()
    return {"updated": n}


@router.post("/devices/import/preview", summary="设备导入预览（调研表）")
async def preview_devices_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    folder = _dir("tmp")
    original, path, _ = _save_file(file, folder)
    try:
        parsed = R.parse_investigation(path)
    except Exception as e:
        raise HTTPException(400, f"解析失败：{e}")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    systems = db.query(NetSystem).all()
    smap = {R.norm(s.name): s for s in systems}
    # 站点关键词兜底
    site_sys = {}
    for s in systems:
        site = db.query(NetSite).filter(NetSite.id == s.site_id).first()
        if site:
            site_sys[site.code] = s
    existing = {}
    for d in db.query(NetDevice).all():
        existing[(d.site_id, R.norm(d.name), R.norm(d.brand_model or ""))] = d
    rows, unknown = [], set()
    for item in parsed["devices"]:
        sys = smap.get(R.norm(item["system_name"]))
        if not sys:
            code = R.guess_site_code(item["system_name"])
            sys = site_sys.get(code)
        if not sys:
            unknown.add(item["system_name"] or "（空）")
            continue
        site = db.query(NetSite).filter(NetSite.id == sys.site_id).first()
        key = (sys.site_id, R.norm(item["name"]), R.norm(item["brand_model"] or ""))
        old = existing.get(key)
        item2 = dict(item)
        item2.update({"_system_id": sys.id, "_system_name": sys.name, "_site_id": sys.site_id,
                      "_site_name": site.name if site else "", "_action": "update" if old else "new",
                      "_existing_id": old.id if old else None,
                      "_key": f"{sys.site_id}|{R.norm(item['name'])}|{R.norm(item['brand_model'] or '')}"})
        rows.append(item2)
    return {"total": len(rows), "new": sum(1 for r in rows if r["_action"] == "new"),
            "update": sum(1 for r in rows if r["_action"] == "update"),
            "skipped": len(parsed["devices"]) - len(rows), "unknown_systems": sorted(unknown),
            "rows": rows, "file_name": original}


@router.post("/devices/import/apply", summary="设备导入应用")
def apply_devices_import(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    rows = payload.get("rows") or []
    ins = upd = 0
    new_ids, updates = [], []
    for r in rows:
        action = r.get("_action")
        if action not in ("new", "update"):
            continue
        data = {k: r.get(k) for k in DEVICE_FIELDS if k in r}
        data["site_id"] = r.get("_site_id") or data.get("site_id")
        data["system_id"] = r.get("_system_id") or data.get("system_id")
        data["source"] = "import"
        if "quantity" in data:
            data["quantity"] = _to_int(data["quantity"]) or 1
        if action == "update" and r.get("_existing_id"):
            d = db.query(NetDevice).filter(NetDevice.id == r["_existing_id"]).first()
            if d:
                before = {k: getattr(d, k, None) for k in data}
                for k, v in data.items():
                    setattr(d, k, v)
                d.updated_at = datetime.utcnow()
                updates.append({"id": d.id, "before": before})
                upd += 1
                continue
        obj = NetDevice(**data)
        db.add(obj)
        db.flush()
        new_ids.append(obj.id)
        ins += 1
    batch = NetImportBatch(kind="device", file_name=_clean(payload.get("file_name"), 300),
                           total=len(rows), inserted=ins, updated=upd, operator=_op(request),
                           detail_json=json.dumps({"new_ids": new_ids, "updates": updates},
                                                  ensure_ascii=False, default=str)[:500000])
    db.add(batch)
    db.commit()
    return {"inserted": ins, "updated": upd, "batch_id": batch.id}


@router.get("/devices/export", summary="导出设备台账")
def export_devices(site_id: Optional[int] = None, system_id: Optional[int] = None,
                   db: Session = Depends(get_db)):
    q = db.query(NetDevice)
    if site_id:
        q = q.filter(NetDevice.site_id == site_id)
    if system_id:
        q = q.filter(NetDevice.system_id == system_id)
    items = q.order_by(NetDevice.site_id, NetDevice.system_id, NetDevice.category).all()
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "设备台账"
    headers = ["ID", "站点", "定级系统", "类别", "设备名称", "品牌及型号", "是否虚拟", "系统及版本",
               "用途", "重要程度", "所属网络区域", "物理环境", "IP地址", "MAC", "是否热备", "数量",
               "业务应用", "数据库", "中间件", "杀毒软件", "资产编号", "SN", "责任人", "机柜",
               "质保到期", "状态", "备注"]
    ws.append(headers)
    for d in items:
        ws.append([d.id, smap.get(d.site_id, ""), sysmap.get(d.system_id, ""), d.category, d.name,
                   d.brand_model or "", d.is_virtual or "", d.os_version or "", d.purpose or "",
                   d.importance or "", d.zone or "", d.room or "", d.mgmt_ip or "", d.mac or "",
                   d.is_hot_standby or "", d.quantity or 1, d.biz_app or "", d.db_version or "",
                   d.middleware or "", d.antivirus or "", d.asset_no or "", d.sn or "", d.owner or "",
                   d.rack or "", d.warranty_end or "", d.status or "", d.remark or ""])
    _style_header(ws)
    for col, w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [6, 14, 26, 12, 26, 24, 8, 16, 20, 8, 12, 14, 16, 18, 8, 6,
                                                     20, 16, 14, 16, 12, 16, 10, 8, 12, 8, 30]):
        ws.column_dimensions[col].width = w
    return _xlsx_response(wb, f"设备台账_{date.today():%Y%m%d}.xlsx")


# ---------------------------------------------------------------- IP/MAC
def _addr_conflicts(items: list) -> dict:
    from collections import defaultdict
    by_ip, by_mac = defaultdict(list), defaultdict(list)
    for a in items:
        if a.get("ip"):
            by_ip[a["ip"]].append(a["id"])
        if a.get("mac"):
            by_mac[a["mac"]].append(a["id"])
    return {"dup_ip": {k: v for k, v in by_ip.items() if len(v) > 1},
            "dup_mac": {k: v for k, v in by_mac.items() if len(v) > 1}}


@router.get("/addresses", summary="IP/MAC 台账列表")
def list_addresses(site_id: Optional[int] = None, department: Optional[str] = None,
                   status: Optional[str] = None, q: Optional[str] = None,
                   conflict_only: bool = False, limit: int = 3000, db: Session = Depends(get_db)):
    query = db.query(NetAddress)
    if site_id:
        query = query.filter(NetAddress.site_id == site_id)
    if department:
        query = query.filter(NetAddress.department == department)
    if status:
        query = query.filter(NetAddress.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(NetAddress.ip.like(like), NetAddress.mac.like(like),
                                 NetAddress.person.like(like), NetAddress.department.like(like),
                                 NetAddress.location.like(like)))
    all_items = query.order_by(NetAddress.ip, NetAddress.id).limit(limit).all()
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    dicts = [_addr_dict(a, smap.get(a.site_id, "")) for a in all_items]
    conflicts = _addr_conflicts(dicts)
    if conflict_only:
        bad = set(conflicts["dup_ip"].keys()) | set(conflicts["dup_mac"].keys())
        dicts = [d for d in dicts if d["ip"] in bad or d["mac"] in bad]
    departments = [r[0] for r in db.query(NetAddress.department).distinct().all() if r[0]]
    return {"total": len(dicts), "items": dicts, "conflicts": conflicts,
            "departments": sorted(departments),
            "statuses": [r[0] for r in db.query(NetAddress.status).distinct().all() if r[0]]}


@router.post("/addresses", summary="新增地址")
def create_address(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    mac, ok = R.normalize_mac(payload.get("mac"))
    a = NetAddress(ip=_clean(payload.get("ip"), 40), mac=mac if ok else None,
                   mac_raw=_clean(payload.get("mac"), 40) if not ok else _clean(payload.get("mac"), 40),
                   department=_clean(payload.get("department"), 60), person=_clean(payload.get("person"), 40),
                   device_type=_clean(payload.get("device_type"), 30), location=_clean(payload.get("location"), 100),
                   site_id=_to_int(payload.get("site_id")), status=_clean(payload.get("status"), 20) or "在用",
                   wangjian=_clean(payload.get("wangjian"), 20), remark=_clean(payload.get("remark"), 2000),
                   source="manual", first_seen=date.today().isoformat(), updated_at=datetime.utcnow())
    db.add(a)
    db.commit()
    db.refresh(a)
    return _addr_dict(a)


@router.put("/addresses/{aid}", summary="编辑地址")
def update_address(aid: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    a = db.query(NetAddress).filter(NetAddress.id == aid).first()
    if not a:
        raise HTTPException(404, "记录不存在")
    for k in ADDR_FIELDS:
        if k in payload:
            new = _to_int(payload[k]) if k == "site_id" else _clean(payload[k], 2000)
            if k == "mac":
                mac, ok = R.normalize_mac(payload[k])
                new = mac if ok else None
            old = getattr(a, k)
            if str(old or "") != str(new or ""):
                _log(db, "address", a.id, a.ip or a.mac or "", k, old, new, _op(request))
                setattr(a, k, new)
    a.updated_at = datetime.utcnow()
    db.commit()
    return _addr_dict(a)


@router.delete("/addresses/{aid}", summary="删除地址")
def delete_address(aid: int, db: Session = Depends(get_db)):
    a = db.query(NetAddress).filter(NetAddress.id == aid).first()
    if not a:
        raise HTTPException(404, "记录不存在")
    db.delete(a)
    db.commit()
    return {"message": "已删除"}


@router.post("/addresses/import/preview", summary="地址导入预览")
async def preview_address_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    folder = _dir("tmp")
    original, path, _ = _save_file(file, folder)
    try:
        rows = R.parse_ip_mac(path)
    except Exception as e:
        raise HTTPException(400, f"解析失败：{e}")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    site_map = {s.code: s for s in db.query(NetSite).all()}
    existing_by_ip = {a.ip: a for a in db.query(NetAddress).filter(NetAddress.ip.isnot(None)).all() if a.ip}
    existing_by_mac = {a.mac: a for a in db.query(NetAddress).filter(NetAddress.mac.isnot(None)).all() if a.mac}
    out = []
    for r in rows:
        site = site_map.get(r.get("site_code") or "")
        old = existing_by_ip.get(r["ip"]) or existing_by_mac.get(r["mac"])
        r2 = dict(r)
        r2.update({"_site_id": site.id if site else None, "_site_name": site.name if site else "",
                   "_action": "update" if old else "new", "_existing_id": old.id if old else None})
        out.append(r2)
    return {"total": len(out), "new": sum(1 for r in out if r["_action"] == "new"),
            "update": sum(1 for r in out if r["_action"] == "update"), "rows": out, "file_name": original}


@router.post("/addresses/import/apply", summary="地址导入应用")
def apply_address_import(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    rows = payload.get("rows") or []
    ins = upd = 0
    new_ids, updates = [], []
    for r in rows:
        if r.get("_action") not in ("new", "update"):
            continue
        data = {k: r.get(k) for k in ADDR_FIELDS if k in r}
        data["site_id"] = r.get("_site_id") or data.get("site_id")
        data["source"] = "import"
        data["updated_at"] = datetime.utcnow()
        if r.get("_action") == "update" and r.get("_existing_id"):
            a = db.query(NetAddress).filter(NetAddress.id == r["_existing_id"]).first()
            if a:
                before = {k: getattr(a, k, None) for k in data if k != "updated_at"}
                for k, v in data.items():
                    setattr(a, k, v)
                updates.append({"id": a.id, "before": before})
                upd += 1
                continue
        obj = NetAddress(**data)
        db.add(obj)
        db.flush()
        new_ids.append(obj.id)
        ins += 1
    batch = NetImportBatch(kind="address", file_name=_clean(payload.get("file_name"), 300),
                           total=len(rows), inserted=ins, updated=upd, operator=_op(request),
                           detail_json=json.dumps({"new_ids": new_ids, "updates": updates},
                                                  ensure_ascii=False, default=str)[:500000])
    db.add(batch)
    db.commit()
    return {"inserted": ins, "updated": upd, "batch_id": batch.id}


@router.post("/addresses/normalize", summary="一键规范化 MAC/空白")
def normalize_addresses(request: Request = None, db: Session = Depends(get_db)):
    fixed = 0
    for a in db.query(NetAddress).all():
        raw = a.mac or a.mac_raw
        mac, ok = R.normalize_mac(raw)
        if ok and mac != a.mac:
            _log(db, "address", a.id, a.ip or "", "mac", a.mac, mac, _op(request), "一键规范化")
            a.mac = mac
            a.mac_raw = a.mac_raw or raw
            fixed += 1
        if a.department and " " in a.department:
            a.department = a.department.replace(" ", "")
    db.commit()
    return {"fixed": fixed}


@router.get("/addresses/export", summary="导出 IP/MAC 台账")
def export_addresses(site_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(NetAddress)
    if site_id:
        q = q.filter(NetAddress.site_id == site_id)
    items = q.order_by(NetAddress.department, NetAddress.ip).all()
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "IP与MAC统计表"
    ws.append(["序号", "部门", "姓名", "办公电脑类型", "使用地点", "IP地址", "MAC地址",
               "状态", "是否安装网鉴V01", "备注", "站点"])
    for i, a in enumerate(items, 1):
        ws.append([i, a.department or "", a.person or "", a.device_type or "", a.location or "",
                   a.ip or "", a.mac or a.mac_raw or "", a.status or "", a.wangjian or "",
                   a.remark or "", smap.get(a.site_id, "")])
    _style_header(ws)
    for col, w in zip("ABCDEFGHIJK", [6, 18, 10, 14, 24, 16, 20, 10, 14, 30, 16]):
        ws.column_dimensions[col].width = w
    return _xlsx_response(wb, f"IP-MAC台账_{date.today():%Y%m%d}.xlsx")


# ---------------------------------------------------------------- 字典归一 / 数据质量
def _norm_label(s) -> str:
    s = str(s or "").replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", s)


def _norm_key(s) -> str:
    return re.sub(r"\s+", "", _norm_label(s))


def _dict_map(db: Session, kind: str) -> dict:
    m = {}
    for d in db.query(NetDict).filter(NetDict.kind == kind).all():
        m[_norm_key(d.name)] = d.name
        for a in (d.alias or "").split(","):
            if a.strip():
                m[_norm_key(a)] = d.name
    return m


def _sync_dicts(db: Session):
    """把地址表中尚未进入字典的部门/地点补为规范项（幂等）。"""
    changed = 0
    for kind in ("department", "location"):
        m = _dict_map(db, kind)
        values = {getattr(a, kind) for a in db.query(NetAddress).all() if getattr(a, kind)}
        for v in values:
            label = _norm_label(v)
            key = _norm_key(label)
            if key and key not in m:
                db.add(NetDict(kind=kind, name=label, alias=None))
                m[key] = label
                changed += 1
    if changed:
        db.commit()


@router.get("/dicts", summary="字典列表")
def list_dicts(kind: Optional[str] = None, db: Session = Depends(get_db)):
    _sync_dicts(db)
    q = db.query(NetDict)
    if kind:
        q = q.filter(NetDict.kind == kind)
    items = q.order_by(NetDict.kind, NetDict.sort_order, NetDict.id).all()
    return [{"id": d.id, "kind": d.kind, "name": d.name, "alias": d.alias or "",
             "sort_order": d.sort_order or 0} for d in items]


@router.post("/dicts", summary="新增/更新字典项")
def create_dict(payload: dict = Body(...), db: Session = Depends(get_db)):
    kind = _clean(payload.get("kind"), 20)
    name = _clean(payload.get("name"), 100)
    alias = _clean(payload.get("alias"), 200)
    if kind not in ("department", "location"):
        raise HTTPException(400, "kind 只能是 department / location")
    if not name:
        raise HTTPException(400, "名称必填")
    row = db.query(NetDict).filter(NetDict.kind == kind, NetDict.name == name).first()
    if row:
        existing = [x.strip() for x in (row.alias or "").split(",") if x.strip()]
        incoming = [x.strip() for x in (alias or "").split(",") if x.strip()]
        merged = []
        for x in existing + incoming:
            if x and x not in merged:
                merged.append(x)
        row.alias = ",".join(merged)
    else:
        db.add(NetDict(kind=kind, name=name, alias=alias))
    db.commit()
    return {"ok": True}


@router.put("/dicts/{did}", summary="编辑字典项")
def update_dict(did: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    row = db.query(NetDict).filter(NetDict.id == did).first()
    if not row:
        raise HTTPException(404, "字典项不存在")
    if "name" in payload:
        row.name = _clean(payload.get("name"), 100) or row.name
    if "alias" in payload:
        row.alias = _clean(payload.get("alias"), 200)
    db.commit()
    return {"ok": True}


@router.delete("/dicts/{did}", summary="删除字典项")
def delete_dict(did: int, db: Session = Depends(get_db)):
    row = db.query(NetDict).filter(NetDict.id == did).first()
    if not row:
        raise HTTPException(404, "字典项不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/dicts/suggest", summary="部门/地点归一候选")
def dict_suggest(kind: str = "department", limit: int = 300, db: Session = Depends(get_db)):
    if kind not in ("department", "location"):
        raise HTTPException(400, "kind 只能是 department / location")
    from collections import Counter
    _sync_dicts(db)
    counter = Counter()
    for a in db.query(NetAddress).all():
        v = _norm_label(getattr(a, kind))
        if v:
            counter[v] += 1
    m = _dict_map(db, kind)
    out = []
    for v, c in counter.most_common(limit):
        target = m.get(_norm_key(v), "")
        out.append({"value": v, "count": c, "target": target or _norm_label(v),
                    "matched": bool(target) and target == v})
    return {"kind": kind, "items": out, "total": len(counter),
            "unmatched": sum(1 for x in out if not x["matched"])}


@router.post("/addresses/apply-dict", summary="按字典归一部门/地点")
def apply_dict(payload: dict = Body(default={}), request: Request = None, db: Session = Depends(get_db)):
    kind = (payload or {}).get("kind", "all")
    kinds = ["department", "location"] if kind == "all" else [kind]
    maps = {k: _dict_map(db, k) for k in kinds if k in ("department", "location")}
    dry = bool((payload or {}).get("dry_run"))
    changed, samples = 0, []
    for a in db.query(NetAddress).all():
        for k, m in maps.items():
            raw = getattr(a, k)
            if not raw:
                continue
            cleaned = _norm_label(raw)
            new = m.get(_norm_key(cleaned), cleaned)
            if new != raw:
                changed += 1
                if len(samples) < 20:
                    samples.append({"id": a.id, "field": k, "old": raw, "new": new})
                if not dry:
                    _log(db, "address", a.id, a.ip or a.mac or "", k, raw, new, _op(request), "字典归一")
                    setattr(a, k, new)
    if dry:
        return {"changed": changed, "dry_run": True, "samples": samples}
    db.commit()
    return {"changed": changed, "dry_run": False, "samples": samples}


@router.get("/quality", summary="数据质量总览")
def data_quality(db: Session = Depends(get_db)):
    from collections import Counter
    _sync_dicts(db)
    addrs = db.query(NetAddress).all()
    devs = db.query(NetDevice).all()
    dm = _dict_map(db, "department")
    lm = _dict_map(db, "location")
    ip_c = Counter(a.ip for a in addrs if a.ip)
    mac_c = Counter(a.mac for a in addrs if a.mac)
    dup_ip = [{"value": k, "count": v} for k, v in ip_c.items() if v > 1]
    dup_mac = [{"value": k, "count": v} for k, v in mac_c.items() if v > 1]
    missing_mac = sum(1 for a in addrs if not a.mac)
    missing_person = sum(1 for a in addrs if not a.person)
    missing_dept = sum(1 for a in addrs if not a.department)
    missing_loc = sum(1 for a in addrs if not a.location)
    invalid_ip = sum(1 for a in addrs if a.ip and not R.is_valid_ip(a.ip))
    un_dept = Counter(_norm_label(a.department) for a in addrs
                      if a.department and _norm_key(a.department) not in dm)
    un_loc = Counter(_norm_label(a.location) for a in addrs
                     if a.location and _norm_key(a.location) not in lm)
    dev_missing_system = sum(1 for d in devs if not d.system_id)
    dev_missing_zone = sum(1 for d in devs if not d.zone)
    dev_missing_owner = sum(1 for d in devs if not d.owner)
    dev_missing_room = sum(1 for d in devs if not d.room)
    score = 100
    score -= len(dup_ip) * 3 + len(dup_mac) * 3
    score -= missing_mac * 0.5 + missing_person * 0.3 + missing_dept * 0.5 + missing_loc * 0.5
    score -= invalid_ip * 2
    score -= sum(un_dept.values()) * 0.2 + sum(un_loc.values()) * 0.05
    score = max(0, round(score))
    return {
        "address": {
            "total": len(addrs), "score": score,
            "dup_ip": dup_ip, "dup_mac": dup_mac,
            "missing_mac": missing_mac, "missing_person": missing_person,
            "missing_department": missing_dept, "missing_location": missing_loc,
            "invalid_ip": invalid_ip,
            "unmatched_department": [{"value": k, "count": v} for k, v in un_dept.most_common()],
            "unmatched_location": [{"value": k, "count": v} for k, v in un_loc.most_common()],
        },
        "device": {
            "total": len(devs), "missing_system": dev_missing_system,
            "missing_zone": dev_missing_zone, "missing_owner": dev_missing_owner,
            "missing_room": dev_missing_room,
        },
        "dict": {"department": len(dm), "location": len(lm)},
    }


# ---------------------------------------------------------------- 拓扑图
@router.get("/topologies", summary="拓扑图列表")
def list_topologies(system_id: Optional[int] = None, site_id: Optional[int] = None,
                    db: Session = Depends(get_db)):
    q = db.query(NetTopology)
    if system_id:
        q = q.filter(NetTopology.system_id == system_id)
    if site_id:
        q = q.filter(NetTopology.site_id == site_id)
    items = q.order_by(NetTopology.system_id, NetTopology.is_current.desc(), NetTopology.id.desc()).all()
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    return [_topo_dict(t, sysmap.get(t.system_id, "")) for t in items]


@router.post("/topologies", summary="上传拓扑图")
async def upload_topology(image: UploadFile = File(...), source: Optional[UploadFile] = File(None),
                          name: Optional[str] = Form(None), system_id: Optional[int] = Form(None),
                          kind: Optional[str] = Form(None), version: Optional[str] = Form(None),
                          effective_date: Optional[str] = Form(None), note: Optional[str] = Form(None),
                          request: Request = None, db: Session = Depends(get_db)):
    original, path, size = _save_file(image, _dir("topology"))
    sys = db.query(NetSystem).filter(NetSystem.id == system_id).first() if system_id else None
    if not sys:
        sys, _ = _match_system(db, original)
    sys_id = sys.id if sys else None
    folder = _dir("topology", str(sys_id or "unbound"))
    final = os.path.join(folder, os.path.basename(path))
    os.replace(path, final)
    # 缩略图（大图用 draft 降采样，避免整幅解码撑爆内存）
    thumb = None
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        im = Image.open(final)
        try:
            im.draft("RGB", (1600, 1600))
        except Exception:
            pass
        im.thumbnail((1600, 1600))
        thumb = final.rsplit(".", 1)[0] + "_thumb.jpg"
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        im.save(thumb, "JPEG", quality=85)
    except Exception as e:
        logger.warning("[网络台账] 生成拓扑缩略图失败：%s", e)
    src_path = None
    if source is not None and source.filename:
        _, src_path, _ = _save_file(source, folder)
    nm = _clean(name, 200) or os.path.splitext(original)[0]
    kd = _clean(kind, 20) or ("接入点图" if "接入点" in original else "网络拓扑图")
    t = NetTopology(site_id=sys.site_id if sys else None, system_id=sys_id, name=nm, kind=kd,
                    version=_clean(version, 20), effective_date=_to_date(effective_date),
                    image_path=final, thumb_path=thumb, source_path=src_path,
                    note=_clean(note, 2000), is_current=1,
                    uploaded_by=_op(request))
    db.add(t)
    db.commit()
    db.refresh(t)
    return _topo_dict(t, sys.name if sys else "")


@router.get("/topologies/{tid}/image", summary="查看拓扑图")
def topology_image(tid: int, thumb: bool = False, db: Session = Depends(get_db)):
    t = db.query(NetTopology).filter(NetTopology.id == tid).first()
    if not t:
        raise HTTPException(404, "拓扑图不存在")
    if thumb and t.thumb_path and os.path.exists(t.thumb_path):
        return FileResponse(t.thumb_path, media_type="image/jpeg")
    return FileResponse(t.image_path)


@router.get("/topologies/{tid}/download", summary="下载拓扑图源文件")
def topology_download(tid: int, source: bool = False, db: Session = Depends(get_db)):
    t = db.query(NetTopology).filter(NetTopology.id == tid).first()
    if not t:
        raise HTTPException(404, "拓扑图不存在")
    path = t.source_path if (source and t.source_path and os.path.exists(t.source_path)) else t.image_path
    return FileResponse(path, filename=os.path.basename(path))


@router.delete("/topologies/{tid}", summary="删除拓扑图")
def delete_topology(tid: int, db: Session = Depends(get_db)):
    t = db.query(NetTopology).filter(NetTopology.id == tid).first()
    if not t:
        raise HTTPException(404, "拓扑图不存在")
    for p in (t.image_path, t.thumb_path, t.source_path):
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    db.delete(t)
    db.commit()
    return {"message": "已删除"}


# ---------------------------------------------------------------- 等保资料
@router.get("/reports", summary="等保资料列表")
def list_reports(system_id: Optional[int] = None, year: Optional[int] = None,
                 doc_type: Optional[str] = None, confirmed: Optional[int] = None,
                 db: Session = Depends(get_db)):
    q = db.query(NetReport)
    if system_id:
        q = q.filter(NetReport.system_id == system_id)
    if year:
        q = q.filter(NetReport.report_year == year)
    if doc_type:
        q = q.filter(NetReport.doc_type == doc_type)
    if confirmed is not None:
        q = q.filter(NetReport.confirmed == confirmed)
    items = q.order_by(NetReport.report_year.desc(), NetReport.system_id, NetReport.doc_type).all()
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    return [_report_dict(r, sysmap.get(r.system_id, ""), smap.get(r.site_id, "")) for r in items]


@router.post("/reports/upload", summary="上传等保资料（自动识别）")
async def upload_reports(files: List[UploadFile] = File(...), system_id: Optional[int] = Form(None),
                         doc_type: Optional[str] = Form(None), year: Optional[int] = Form(None),
                         request: Request = None, db: Session = Depends(get_db)):
    out = []
    for f in files:
        original, tmp, size = _save_file(f, _dir("tmp"))
        meta = R.parse_report_meta(tmp, original)
        sys = db.query(NetSystem).filter(NetSystem.id == system_id).first() if system_id else None
        if not sys:
            sys, _ = _match_system(db, f"{meta.get('system_name','')} {original}")
        if not sys:
            os.remove(tmp)
            out.append({"ok": False, "file": original, "error": "未能匹配定级系统，请手动指定"})
            continue
        dt = doc_type or meta.get("doc_type") or "其他"
        ry = year or meta.get("report_year") or (int((meta.get("eval_end") or "")[:4]) if meta.get("eval_end") else None) or date.today().year
        folder = _dir("reports", str(sys.id), str(ry))
        ext = os.path.splitext(original)[1].lower()
        final = os.path.join(folder, f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}{ext}")
        os.replace(tmp, final)
        role = "主件" if ext == ".pdf" else "附件"
        level = meta.get("level") or (sys.level or "")
        r = NetReport(system_id=sys.id, site_id=sys.site_id, doc_type=dt, report_year=ry,
                      project_no=_clean(meta.get("project_no"), 40),
                      report_no=_clean(meta.get("project_no"), 60),
                      level=_clean(level, 10), level_s=_clean(meta.get("level_s"), 5),
                      level_a=_clean(meta.get("level_a"), 5), agency=_clean(meta.get("agency"), 120),
                      eval_start=_to_date(meta.get("eval_start")), eval_end=_to_date(meta.get("eval_end")),
                      report_date=_to_date(meta.get("report_date")),
                      conclusion=_clean(meta.get("conclusion"), 20),
                      pass_rate=_to_float(meta.get("pass_rate")),
                      item_total=_to_int(meta.get("item_total")), item_pass=_to_int(meta.get("item_pass")),
                      item_partial=_to_int(meta.get("item_partial")), item_fail=_to_int(meta.get("item_fail")),
                      item_na=_to_int(meta.get("item_na")),
                      problem_count=_to_int(meta.get("problem_count")),
                      major_risk=_to_int(meta.get("major_risk")) or 0,
                      last_eval_date=_to_date(meta.get("last_eval_date")),
                      last_score=_to_float(meta.get("last_score")),
                      last_conclusion=_clean(meta.get("last_conclusion"), 20),
                      next_due=_next_eval(meta.get("eval_end") or "", level),
                      file_path=final, original_name=original, file_role=role, size=size,
                      auto_type=_clean(meta.get("doc_type"), 20), auto_system=_clean(meta.get("system_name"), 200),
                      auto_year=ry, confidence=_to_int(meta.get("confidence")) or 0,
                      confirmed=0, remark=_clean(meta.get("parse_error"), 2000))
        db.add(r)
        db.commit()
        db.refresh(r)
        out.append({"ok": True, "file": original, "id": r.id, "system_name": sys.name,
                    "doc_type": dt, "year": ry, "confidence": r.confidence,
                    "level": level, "pass_rate": r.pass_rate, "conclusion": r.conclusion})
    return {"results": out}


@router.put("/reports/{rid}", summary="编辑等保资料")
def update_report(rid: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    r = db.query(NetReport).filter(NetReport.id == rid).first()
    if not r:
        raise HTTPException(404, "资料不存在")
    for k in REPORT_FIELDS:
        if k in payload:
            if k in ("system_id", "site_id", "report_year", "item_total", "item_pass", "item_partial",
                     "item_fail", "item_na", "problem_count", "major_risk", "confirmed"):
                new = _to_int(payload[k])
            elif k in ("pass_rate", "last_score"):
                new = _to_float(payload[k])
            elif k in ("eval_start", "eval_end", "report_date", "last_eval_date", "next_due"):
                new = _to_date(payload[k])
            else:
                new = _clean(payload[k], 2000)
            old = getattr(r, k)
            if str(old or "") != str(new or ""):
                _log(db, "report", r.id, r.original_name, k, old, new, _op(request))
                setattr(r, k, new)
    # 确认后同步系统测评信息
    if _to_int(payload.get("confirmed")) == 1:
        r.confirmed = 1
        sys = db.query(NetSystem).filter(NetSystem.id == r.system_id).first()
        if sys and r.doc_type == "测评报告":
            if r.eval_end:
                sys.last_eval_date = r.eval_end
            if r.conclusion:
                sys.last_conclusion = r.conclusion
            if not sys.next_eval_date:
                sys.next_eval_date = _next_eval(r.eval_end or "", r.level or sys.level or "")
    db.commit()
    return _report_dict(r)


@router.delete("/reports/{rid}", summary="删除等保资料")
def delete_report(rid: int, db: Session = Depends(get_db)):
    r = db.query(NetReport).filter(NetReport.id == rid).first()
    if not r:
        raise HTTPException(404, "资料不存在")
    if r.file_path and os.path.exists(r.file_path):
        try:
            os.remove(r.file_path)
        except Exception:
            pass
    db.query(NetFinding).filter(NetFinding.report_id == rid).delete()
    db.delete(r)
    db.commit()
    return {"message": "已删除"}


@router.get("/reports/{rid}/download", summary="下载等保资料")
def download_report(rid: int, db: Session = Depends(get_db)):
    r = db.query(NetReport).filter(NetReport.id == rid).first()
    if not r or not os.path.exists(r.file_path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(r.file_path, filename=r.original_name)


@router.get("/reports/summary", summary="年度测评结果汇总")
def reports_summary(year: int = 2026, db: Session = Depends(get_db)):
    reports = db.query(NetReport).filter(NetReport.doc_type == "测评报告",
                                         NetReport.report_year == year).all()
    sysmap = {s.id: s for s in db.query(NetSystem).all()}
    rows = {}
    for r in reports:
        prev = rows.get(r.system_id)
        score = ((r.confirmed or 0), (r.problem_count or 0), 1 if r.pass_rate is not None else 0)
        if prev is None or score > ((prev.confirmed or 0), (prev.problem_count or 0), 1 if prev.pass_rate is not None else 0):
            rows[r.system_id] = r
    out, rates, problems = [], [], 0
    for sid, r in rows.items():
        s = sysmap.get(sid)
        out.append({"system_id": sid, "system_name": s.name if s else "", "level": r.level or (s.level if s else ""),
                    "pass_rate": r.pass_rate, "problem_count": r.problem_count or 0,
                    "conclusion": r.conclusion or "", "major_risk": int(r.major_risk or 0),
                    "eval_end": r.eval_end or "", "confirmed": int(r.confirmed or 0)})
        if r.pass_rate:
            rates.append(r.pass_rate)
        problems += r.problem_count or 0
    out.sort(key=lambda x: (x["pass_rate"] is None, x["pass_rate"] or 0))
    return {"year": year, "systems": out, "count": len(out),
            "avg_pass_rate": round(sum(rates) / len(rates), 2) if rates else None,
            "problem_total": problems}


@router.post("/reports/{rid}/findings/preview", summary="解析整改清单")
def preview_findings(rid: int, db: Session = Depends(get_db)):
    r = db.query(NetReport).filter(NetReport.id == rid).first()
    if not r:
        raise HTTPException(404, "资料不存在")
    docx_path = r.file_path if r.file_path.lower().endswith(".docx") else None
    if not docx_path:
        folder = os.path.dirname(r.file_path)
        for fn in os.listdir(folder) if os.path.isdir(folder) else []:
            if fn.lower().endswith(".docx") and "报告" in fn:
                docx_path = os.path.join(folder, fn)
                break
    if not docx_path or not os.path.exists(docx_path):
        raise HTTPException(400, "未找到可解析的 docx 报告（PDF 无法解析整改清单）")
    try:
        items = R.extract_findings(docx_path)
    except Exception as e:
        raise HTTPException(400, f"解析失败：{e}")
    return {"report_id": rid, "count": len(items), "items": items}


@router.post("/reports/{rid}/findings/apply", summary="导入整改清单")
def apply_findings(rid: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    r = db.query(NetReport).filter(NetReport.id == rid).first()
    if not r:
        raise HTTPException(404, "资料不存在")
    items = payload.get("items") or []
    if payload.get("replace"):
        db.query(NetFinding).filter(NetFinding.report_id == rid).delete()
    default_plan = (date.today() + timedelta(days=90)).isoformat()
    n = 0
    for it in items:
        db.add(NetFinding(report_id=rid, system_id=r.system_id,
                          problem_no=_to_int(it.get("problem_no")),
                          security_class=_clean(it.get("security_class"), 30),
                          control_point=_clean(it.get("control_point"), 30),
                          target=_clean(it.get("target"), 300),
                          requirement=_clean(it.get("requirement"), 4000),
                          description=_clean(it.get("description"), 4000) or "（无描述）",
                          risk_level=_clean(it.get("risk_level"), 10),
                          suggestion=_clean(it.get("suggestion"), 4000),
                          source=_clean(it.get("source"), 20) or "report",
                          prev_status=_clean(it.get("prev_status"), 20),
                          status="待整改", plan_date=default_plan))
        n += 1
    r.problem_count = len(items)
    db.commit()
    return {"inserted": n, "report_id": rid}


# ---------------------------------------------------------------- 整改项
@router.get("/findings", summary="整改项列表")
def list_findings(system_id: Optional[int] = None, status: Optional[str] = None,
                  risk_level: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(NetFinding)
    if system_id:
        q = q.filter(NetFinding.system_id == system_id)
    if status:
        q = q.filter(NetFinding.status == status)
    if risk_level:
        q = q.filter(NetFinding.risk_level == risk_level)
    items = q.order_by(NetFinding.system_id, NetFinding.problem_no).all()
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    return [_finding_dict(f, sysmap.get(f.system_id, "")) for f in items]


@router.put("/findings/{fid}", summary="更新整改项")
def update_finding(fid: int, payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    f = db.query(NetFinding).filter(NetFinding.id == fid).first()
    if not f:
        raise HTTPException(404, "整改项不存在")
    for k in ("status", "owner", "plan_date", "done_date", "suggestion", "remark", "risk_level",
              "security_class", "control_point", "target", "description"):
        if k in payload:
            new = _to_date(payload[k]) if k in ("plan_date", "done_date") else _clean(payload[k], 4000)
            old = getattr(f, k)
            if str(old or "") != str(new or ""):
                _log(db, "finding", f.id, f"问题{f.problem_no}", k, old, new, _op(request))
                setattr(f, k, new)
    db.commit()
    return _finding_dict(f)


@router.delete("/findings/{fid}", summary="删除整改项")
def delete_finding(fid: int, db: Session = Depends(get_db)):
    f = db.query(NetFinding).filter(NetFinding.id == fid).first()
    if not f:
        raise HTTPException(404, "整改项不存在")
    db.delete(f)
    db.commit()
    return {"message": "已删除"}


@router.post("/findings/batch-update", summary="批量更新整改项")
def batch_update_findings(payload: dict = Body(...), request: Request = None, db: Session = Depends(get_db)):
    ids = payload.get("ids") or []
    if not ids:
        raise HTTPException(400, "请选择整改项")
    fields = {}
    for k in ("status", "owner", "risk_level"):
        if payload.get(k):
            fields[k] = _clean(payload[k], 200)
    if payload.get("plan_date"):
        fields["plan_date"] = _to_date(payload["plan_date"])
    if not fields:
        raise HTTPException(400, "没有可更新的字段")
    n = 0
    for f in db.query(NetFinding).filter(NetFinding.id.in_(ids)).all():
        for k, v in fields.items():
            old = getattr(f, k)
            if str(old or "") != str(v or ""):
                _log(db, "finding", f.id, f"问题{f.problem_no}", k, old, v, _op(request), "批量修改")
                setattr(f, k, v)
        n += 1
    db.commit()
    return {"updated": n}


@router.get("/import-batches", summary="导入批次列表")
def list_import_batches(kind: Optional[str] = None, limit: int = 30, db: Session = Depends(get_db)):
    q = db.query(NetImportBatch)
    if kind:
        q = q.filter(NetImportBatch.kind == kind)
    items = q.order_by(NetImportBatch.id.desc()).limit(limit).all()
    out = []
    for b in items:
        detail = {}
        try:
            detail = json.loads(b.detail_json or "{}")
        except Exception:
            detail = {}
        out.append({"id": b.id, "kind": b.kind, "file_name": b.file_name or "",
                    "total": b.total or 0, "inserted": b.inserted or 0, "updated": b.updated or 0,
                    "operator": b.operator or "", "rollbackable": isinstance(detail, dict) and
                    ("new_ids" in detail or "updates" in detail),
                    "created_at": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else ""})
    return out


@router.post("/import-batches/{bid}/rollback", summary="回滚导入批次")
def rollback_import_batch(bid: int, request: Request = None, db: Session = Depends(get_db)):
    b = db.query(NetImportBatch).filter(NetImportBatch.id == bid).first()
    if not b:
        raise HTTPException(404, "批次不存在")
    try:
        detail = json.loads(b.detail_json or "{}")
    except Exception:
        detail = {}
    if not isinstance(detail, dict) or not ("new_ids" in detail or "updates" in detail):
        raise HTTPException(400, "该批次是旧格式（未保存快照），无法自动回滚")
    model = NetDevice if b.kind == "device" else NetAddress
    deleted = restored = 0
    for nid in detail.get("new_ids", []):
        obj = db.query(model).filter(model.id == nid).first()
        if obj:
            db.delete(obj)
            deleted += 1
    for u in detail.get("updates", []):
        obj = db.query(model).filter(model.id == u.get("id")).first()
        if not obj:
            continue
        for k, v in (u.get("before") or {}).items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        restored += 1
    db.delete(b)
    db.commit()
    return {"deleted": deleted, "restored": restored}


@router.get("/findings/export", summary="导出整改清单")
def export_findings(system_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(NetFinding)
    if system_id:
        q = q.filter(NetFinding.system_id == system_id)
    items = q.order_by(NetFinding.system_id, NetFinding.problem_no).all()
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "整改清单"
    ws.append(["系统", "问题编号", "安全类", "控制点", "测评对象", "安全问题", "风险等级",
               "整改建议", "前次状态", "整改状态", "责任人", "计划完成", "实际完成", "备注"])
    for f in items:
        ws.append([sysmap.get(f.system_id, ""), f.problem_no, f.security_class or "", f.control_point or "",
                   f.target or "", f.description, f.risk_level or "", f.suggestion or "",
                   f.prev_status or "", f.status or "", f.owner or "", f.plan_date or "",
                   f.done_date or "", f.remark or ""])
    _style_header(ws)
    for col, w in zip("ABCDEFGHIJKLMN", [26, 8, 12, 12, 24, 50, 8, 50, 10, 10, 10, 12, 12, 20]):
        ws.column_dimensions[col].width = w
    return _xlsx_response(wb, f"整改清单_{date.today():%Y%m%d}.xlsx")


@router.get("/export/investigation", summary="反向生成等保调研表")
def export_investigation(company: Optional[str] = None, system_id: Optional[int] = None,
                         db: Session = Depends(get_db)):
    if system_id:
        sysrow = db.query(NetSystem).filter(NetSystem.id == system_id).first()
        if not sysrow:
            raise HTTPException(404, "系统不存在")
        site = db.query(NetSite).filter(NetSite.id == sysrow.site_id).first()
        company = site.company if site else company
        systems = [sysrow]
        devices = db.query(NetDevice).filter(NetDevice.system_id == system_id).all()
    else:
        if not company:
            raise HTTPException(400, "请指定公司或系统")
        sites = db.query(NetSite).filter(NetSite.company == company).all()
        site_ids = [s.id for s in sites]
        systems = db.query(NetSystem).filter(NetSystem.site_id.in_(site_ids)).all() if site_ids else []
        devices = db.query(NetDevice).filter(NetDevice.site_id.in_(site_ids)).all() if site_ids else []
    if not systems:
        raise HTTPException(400, "该公司下没有定级系统")
    if company not in net_forms.TEMPLATES:
        raise HTTPException(400, f"未准备 {company} 的调研表模板")
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    sysmap = {s.id: s.name for s in db.query(NetSystem).all()}
    system_dicts = [{"id": s.id, "name": s.name, "level": s.level or "",
                     "level_s": s.level_s or "", "level_a": s.level_a or "",
                     "deploy_zone": s.deploy_zone or "", "filing_no": s.filing_no or "",
                     "extension": s.extension or "", "integrator": s.integrator or ""}
                    for s in systems]
    device_dicts = [_device_dict(d, smap.get(d.site_id, ""), sysmap.get(d.system_id, "")) for d in devices]
    try:
        data, filename = net_forms.build_investigation(company, system_dicts, device_dicts, system_id)
    except Exception as e:
        logger.exception("[网络台账] 调研表生成失败")
        raise HTTPException(500, f"生成失败：{e}")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# ---------------------------------------------------------------- 本地智能问答（不调用 AI）
def _local_match_site(q, sites):
    t = R.norm(q)
    for s in sites:
        if R.norm(s.name) in t or R.norm(s.code) in t:
            return s
    code = R.guess_site_code(t)
    return next((s for s in sites if s.code == code), None)


def _local_match_system(q, systems):
    t = R.norm(q)
    for s in systems:
        if R.norm(s.name) in t:
            return s
    for s in systems:
        kw = re.sub(r"(电站|风电场|光伏|开关站)?(计算机监控系统|电力监控系统|监控系统)", "", s.name)
        if kw and R.norm(kw) in t:
            return s
    return None


def _local_latest_reports(reps):
    m = {}
    for r in reps:
        if r.system_id not in m or (r.problem_count or 0) > (m[r.system_id].problem_count or 0):
            m[r.system_id] = r
    return list(m.values())


def local_answer(question: str, db: Session = None):
    """基于结构化台账的本地问答：统计/检索/合规结果，不调用任何外部 AI。"""
    own = db is None
    db = db or SessionLocal()
    try:
        q = (question or "").strip()
        if not q:
            return None
        sites = db.query(NetSite).all()
        systems = db.query(NetSystem).all()
        devices = db.query(NetDevice).all()
        addrs = db.query(NetAddress).all()
        reports = db.query(NetReport).all()
        findings = db.query(NetFinding).all()
        topos = db.query(NetTopology).all()
        site = _local_match_site(q, sites)
        sysrow = _local_match_system(q, systems)
        sources = []
        if site:
            sources.append(f"站点：{site.name}")
        if sysrow:
            sources.append(f"系统：{sysrow.name}")

        # 1) IP/MAC 冲突/查询
        if any(k in q for k in ("冲突", "重复")) and any(k in q.upper() for k in ("IP", "MAC", "地址")):
            ip_c, mac_c = {}, {}
            for a in addrs:
                if a.ip:
                    ip_c.setdefault(a.ip, []).append(a)
                if a.mac:
                    mac_c.setdefault(a.mac, []).append(a)
            dup_ip = [(k, v) for k, v in ip_c.items() if len(v) > 1]
            dup_mac = [(k, v) for k, v in mac_c.items() if len(v) > 1]
            lines = [f"当前共 {len(dup_ip)} 组重复 IP、{len(dup_mac)} 组重复 MAC："]
            for k, v in dup_ip:
                lines.append(f"- IP {k}：{len(v)} 条（" + "、".join(x.person or x.department or x.location or "未填" for x in v[:4]) + "）")
            for k, v in dup_mac:
                lines.append(f"- MAC {k}：{len(v)} 条")
            return {"answer": "\n".join(lines), "sources": sources, "mode": "local"}

        # 2) 等保/测评/符合率/备案
        if any(k in q for k in ("等保", "测评", "符合率", "测评结论", "备案", "得分")):
            ym = re.search(r"(20\d{2})", q)
            year = int(ym.group(1)) if ym else 2026
            reps = [r for r in reports if r.doc_type == "测评报告" and r.report_year == year]
            if sysrow:
                reps = [r for r in reps if r.system_id == sysrow.id]
            reps = _local_latest_reports(reps)
            if not reps:
                return {"answer": f"{year} 年暂未录入测评报告。可在「等保资料」上传，或查看其他年度。",
                        "sources": sources, "mode": "local"}
            if ("最低" in q or "最差" in q) and not sysrow:
                r = min(reps, key=lambda x: x.pass_rate or 999)
                return {"answer": f"{year} 年符合率最低：{next((s.name for s in systems if s.id == r.system_id), '')}，"
                                  f"符合率 {r.pass_rate}%，结论 {r.conclusion or '—'}，问题 {r.problem_count or 0} 项。",
                        "sources": sources, "mode": "local"}
            if sysrow:
                r = reps[0]
                return {"answer": f"{sysrow.name}（{r.level or sysrow.level or '—'}）{year} 年测评：\n"
                                  f"- 符合率：{r.pass_rate}%\n- 结论：{r.conclusion or '—'}\n"
                                  f"- 问题数：{r.problem_count or 0} 项\n- 测评结束：{r.eval_end or '—'}\n"
                                  f"- 备案号：{sysrow.filing_no or '—'}",
                        "sources": sources, "mode": "local"}
            lines = [f"{year} 年共 {len(reps)} 个系统有测评报告："]
            for r in sorted(reps, key=lambda x: x.pass_rate or 999):
                nm = next((s.name for s in systems if s.id == r.system_id), "")
                lines.append(f"- {nm}：{r.pass_rate}%，{r.conclusion or '—'}，问题 {r.problem_count or 0} 项")
            return {"answer": "\n".join(lines), "sources": sources, "mode": "local"}

        # 3) 整改/问题/逾期
        if any(k in q for k in ("整改", "问题", "逾期", "闭环")):
            fs = [f for f in findings if (not sysrow or f.system_id == sysrow.id)]
            done = {"已整改", "已验收"}
            open_fs = [f for f in fs if f.status not in done]
            overdue = [f for f in open_fs if f.plan_date and f.plan_date < date.today().isoformat()]
            lines = [f"整改项共 {len(fs)} 项：未完成 {len(open_fs)} 项，已逾期 {len(overdue)} 项。"]
            by_status = {}
            for f in fs:
                by_status[f.status or "未填"] = by_status.get(f.status or "未填", 0) + 1
            lines.append("状态分布：" + "、".join(f"{k} {v}" for k, v in by_status.items()))
            for f in sorted(overdue, key=lambda x: x.plan_date or "")[:8]:
                nm = next((s.name for s in systems if s.id == f.system_id), "")
                lines.append(f"- 逾期：{nm} 问题{f.problem_no or ''}（计划 {f.plan_date}）{(f.description or '')[:40]}")
            return {"answer": "\n".join(lines), "sources": sources, "mode": "local"}

        # 4) 拓扑图
        if "拓扑" in q:
            ts = [t for t in topos if (not sysrow or t.system_id == sysrow.id) and (not site or t.site_id == site.id)]
            if not ts:
                nm = (sysrow.name if sysrow else (site.name if site else "")) or "该范围"
                return {"answer": f"{nm}暂无拓扑图。", "sources": sources, "mode": "local"}
            lines = [f"共 {len(ts)} 张拓扑图："]
            for t in ts[:10]:
                nm = next((s.name for s in systems if s.id == t.system_id), "")
                lines.append(f"- {t.name}（{nm}，{t.kind}，版本 {t.version or '—'}）")
            return {"answer": "\n".join(lines), "sources": sources, "mode": "local"}

        # 5) IP/MAC 精确查询
        ips = re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", q)
        macs = re.findall(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}", q)
        if ips or macs:
            hits = []
            for a in addrs:
                if a.ip and any(x in a.ip for x in ips):
                    hits.append(a)
                elif a.mac and any(x.lower() in a.mac.lower() for x in macs):
                    hits.append(a)
            if hits:
                lines = [f"查到 {len(hits)} 条地址记录："]
                for a in hits[:10]:
                    lines.append(f"- {a.ip or '—'} / {a.mac or '—'}｜{a.person or '—'}｜{a.department or '—'}｜{a.location or '—'}｜{a.status or '—'}")
                return {"answer": "\n".join(lines), "sources": sources, "mode": "local"}
            return {"answer": "未在地址台账中找到该 IP/MAC。", "sources": sources, "mode": "local"}

        # 6) 设备统计/查询
        if any(k in q for k in ("设备", "交换机", "防火墙", "服务器", "终端", "多少", "几台", "统计")):
            ds = devices
            if site:
                ds = [d for d in ds if d.site_id == site.id]
            if sysrow:
                ds = [d for d in ds if d.system_id == sysrow.id]
            if "交换机" in q:
                ds = [d for d in ds if "交换机" in (d.name or "")]
            elif "防火墙" in q:
                ds = [d for d in ds if "防火墙" in (d.name or "") or d.category == "安全设备"]
            elif "服务器" in q or "存储" in q:
                ds = [d for d in ds if d.category == "服务器存储"]
            elif "终端" in q or "电脑" in q or "工作站" in q:
                ds = [d for d in ds if d.category == "终端设备"]
            for st in ("报废", "备用", "维修", "在用"):
                if st in q:
                    ds = [d for d in ds if d.status == st]
            cats = {}
            for d in ds:
                cats[d.category] = cats.get(d.category, 0) + 1
            scope = (site.name if site else "") + (sysrow.name if sysrow else "") or "全公司"
            lines = [f"{scope} 匹配设备 {len(ds)} 台。"]
            if cats:
                lines.append("按类别：" + "、".join(f"{k} {v}" for k, v in cats.items()))
            for d in ds[:10]:
                lines.append(f"- {d.name}（{d.category}，{d.brand_model or '—'}，{d.zone or '—'}，{d.status or '—'}）")
            if len(ds) > 10:
                lines.append(f"……仅列前 10 台。")
            return {"answer": "\n".join(lines), "sources": sources, "mode": "local"}

        # 7) 站点/系统清单
        if "站点" in q:
            return {"answer": "共 " + str(len(sites)) + " 个站点：" + "、".join(s.name for s in sites),
                    "sources": sources, "mode": "local"}
        if "系统" in q and not sysrow:
            return {"answer": "共 " + str(len(systems)) + " 个定级系统：" + "、".join(s.name for s in systems),
                    "sources": sources, "mode": "local"}

        # 8) 兜底关键词检索
        terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}", q) if t not in ("多少", "哪些", "什么", "怎么", "是否", "有没有")]
        hits = []
        for d in devices:
            blob = f"{d.name}{d.brand_model or ''}{d.zone or ''}{d.owner or ''}"
            if any(t in blob for t in terms[:5]):
                hits.append(f"设备：{d.name}（{d.category}，{d.brand_model or '—'}）")
            if len(hits) >= 8:
                break
        for f in findings:
            if any(t in (f.description or "") for t in terms[:5]):
                nm = next((s.name for s in systems if s.id == f.system_id), "")
                hits.append(f"整改：{nm} 问题{f.problem_no or ''}（{f.status}）{(f.description or '')[:40]}")
            if len(hits) >= 12:
                break
        if hits:
            return {"answer": "本地检索到：\n" + "\n".join(f"- {h}" for h in hits), "sources": sources, "mode": "local"}
        return None
    except Exception as e:
        logger.warning("[本地问答] 失败：%s", e)
        return None
    finally:
        if own:
            db.close()


@router.post("/ask", summary="网络台账本地智能问答（不调用 AI）")
def net_local_ask(payload: dict = Body(...)):
    q = (payload or {}).get("question", "")
    res = local_answer(q)
    if not res:
        return {"answer": "本地台账中未找到直接答案。可尝试：设备数量、IP 冲突、等保符合率、整改逾期、拓扑图、站点清单。",
                "sources": [], "mode": "local"}
    return res


# ---------------------------------------------------------------- 统计 / 质量 / 变更
@router.get("/overview", summary="网络台账总览")
def overview(db: Session = Depends(get_db)):
    sites = db.query(NetSite).count()
    systems = db.query(NetSystem).count()
    devices = db.query(NetDevice).count()
    addresses = db.query(NetAddress).all()
    reports = db.query(NetReport).count()
    findings_open = db.query(NetFinding).filter(NetFinding.status != "已验收").count()
    by_cat = dict(db.query(NetDevice.category, func.count(NetDevice.id)).group_by(NetDevice.category).all())
    by_site = dict(db.query(NetDevice.site_id, func.count(NetDevice.id)).group_by(NetDevice.site_id).all())
    smap = {s.id: s.name for s in db.query(NetSite).all()}
    q = R.quality_report([_addr_dict(a) for a in addresses])
    # 2026 测评汇总（按系统去重，docx/pdf 只算一次）
    rep_all = db.query(NetReport).filter(NetReport.doc_type == "测评报告", NetReport.report_year == 2026).all()
    rep = {}
    for r in rep_all:
        prev = rep.get(r.system_id)
        if prev is None or (r.problem_count or 0) > (prev.problem_count or 0):
            rep[r.system_id] = r
    rep = list(rep.values())
    rates = [r.pass_rate for r in rep if r.pass_rate]
    problems = sum(r.problem_count or 0 for r in rep)
    # 到期
    today = date.today()
    upcoming = []
    for s in db.query(NetSystem).all():
        if not s.next_eval_date:
            continue
        try:
            y, m, dd = [int(x) for x in re.split(r"[-/.]", s.next_eval_date)[:3]]
            days = (date(y, m, dd) - today).days
        except Exception:
            continue
        if days <= 180:
            upcoming.append({"system_id": s.id, "system_name": s.name, "next_eval_date": s.next_eval_date,
                             "days": days, "level": s.level or ""})
    upcoming.sort(key=lambda x: x["days"])
    changes = db.query(NetChange).order_by(NetChange.id.desc()).limit(10).all()
    return {
        "sites": sites, "systems": systems, "devices": devices, "addresses": len(addresses),
        "reports": reports, "findings_open": findings_open,
        "device_by_category": [{"category": k, "count": v} for k, v in by_cat.items()],
        "device_by_site": [{"site_name": smap.get(k, ""), "count": v} for k, v in by_site.items()],
        "address_quality": q,
        "eval_2026": {"count": len(rep), "avg_pass_rate": round(sum(rates) / len(rates), 2) if rates else None,
                      "problem_total": problems,
                      "low": sorted([{"system_name": next((s.name for s in db.query(NetSystem).all() if s.id == r.system_id), ""),
                                      "pass_rate": r.pass_rate} for r in rep if r.pass_rate],
                                    key=lambda x: x["pass_rate"])[:3]},
        "upcoming": upcoming[:10],
        "recent_changes": [{"entity_type": c.entity_type, "label": c.entity_label, "field": c.field,
                            "old": c.old_value, "new": c.new_value, "operator": c.operator,
                            "ts": c.ts.strftime("%Y-%m-%d %H:%M") if c.ts else ""} for c in changes],
    }


@router.get("/compliance-matrix", summary="等保合规矩阵")
def compliance_matrix(years: str = "2024,2025,2026", db: Session = Depends(get_db)):
    year_list = [int(y) for y in years.split(",") if y.strip().isdigit()]
    systems = db.query(NetSystem).order_by(NetSystem.site_id, NetSystem.id).all()
    reports = db.query(NetReport).all()
    types = ["测评报告", "备案证明", "整改报告", "自查报告"]
    rows = []
    for s in systems:
        cells = {}
        for y in year_list:
            cells[str(y)] = {}
            for t in types:
                # 备案证明长期有效：只要存在即算有；其余按年度
                has = any(r.system_id == s.id and r.doc_type == t and
                          (r.report_year == y or t == "备案证明") for r in reports)
                cells[str(y)][t] = has
        rows.append({"system_id": s.id, "system_name": s.name, "level": s.level or "",
                     "cells": cells})
    return {"years": year_list, "types": types, "rows": rows}


@router.get("/changes", summary="变更日志")
def list_changes(limit: int = 200, db: Session = Depends(get_db)):
    items = db.query(NetChange).order_by(NetChange.id.desc()).limit(limit).all()
    return [{"id": c.id, "entity_type": c.entity_type, "entity_id": c.entity_id,
             "label": c.entity_label, "field": c.field, "old": c.old_value, "new": c.new_value,
             "reason": c.reason, "operator": c.operator,
             "ts": c.ts.strftime("%Y-%m-%d %H:%M:%S") if c.ts else ""} for c in items]
