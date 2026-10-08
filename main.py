"""
研发知识智能管理平台 - MVP 第一版
功能：项目管理 + 文件上传/下载/列表 + 文件内容解析
技术栈：FastAPI + SQLite + SQLAlchemy + 本地文件存储
"""

import os
import re
import json
import shutil
import requests
import zipfile
import hmac
import time
import glob
import difflib
from urllib.parse import quote
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Request, Form
from fastapi.responses import FileResponse, Response, JSONResponse
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from constants import PROJECT_STAGES, AMOUNT_KINDS
from utils.text_extract import extract_text_from_file, is_supported_file, _pdf_needs_ocr
from config import BASE_DIR, ENV, UPLOAD_DIR, EXTRACT_DIR, DATABASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_API_URL, logger, _append_env
from utils.storage import _content_path, _save_content, _load_content
from utils.ai import _fallback_terms, _expand_query, _chunk_text, _call_deepseek
from database import engine, SessionLocal, Base, get_db
from models import (
    Project, ProjectFile, ContractSummary, DocumentSummary, Contract,
    ContractNode, ContractNodeChange, FinancialDoc, Todo, SystemConfig,
    AuditLog, Funding, FundingGridRow, FundingCell, FundingAttachment,
    Achievement, ProjectContractAmountOverride, AlertDismissed,
)
import security

import io
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import fitz  # PyMuPDF
from rapidocr_onnxruntime import RapidOCR
from openpyxl import load_workbook


# OCR 后台线程池：专门跑扫描件 OCR / 合同 / 财务 / 成果 等异步识别，避免上传阻塞
_ocr_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr-bg")


from utils.audit_backup import _audit, start_maintenance_thread

start_maintenance_thread()

# ---- 检索/回答相关参数（_chunk_text 已抽到 utils/ai.py）----
MAX_CHUNKS = 40          # 全局最多取多少块（喂给 LLM 的上限）
PER_FILE_CHUNKS = 8      # 单个文件最多贡献多少块，避免一个超长文件霸占名额


def _retrieve_context(question: str, top_k: int = 5, project_id: Optional[int] = None):
    """分块检索：把每个文件切成带重叠的文本块，逐块按关键词打分，取全局最高分的若干块。
    这样大文件的任意相关段落都能被命中（而不是只截取文件开头），多个文件也能公平竞争。
    返回 (chunks, references)，chunks 为 [{label, text, file_id, ...}] 列表。"""
    question = (question or "").strip()
    if not question:
        return [], []

    # 1) 检索词：优先 LLM 扩展，失败退回拆词
    keywords = []
    try:
        keywords = _expand_query(question)
    except Exception as e:
        logger.warning(f"[检索] 关键词扩展失败，退回拆词：{e}")
    if not keywords:
        keywords = _fallback_terms(question)
    kw_lower = [k.lower() for k in keywords if k]

    max_chunks = MAX_CHUNKS
    if top_k and top_k > 0:
        max_chunks = max(1, min(top_k, MAX_CHUNKS))

    db = SessionLocal()
    try:
        query = (
            db.query(ProjectFile, Project)
            .join(Project, ProjectFile.project_id == Project.id)
        )
        if project_id is not None:
            query = query.filter(ProjectFile.project_id == project_id)
        rows = query.all()

        # 2) 逐块打分（词频加权，文件名命中给少量加成）
        scored_chunks = []
        for f, p in rows:
            try:
                content = _load_content(f)
            except Exception as e:
                logger.warning(f"[检索] 读取文件内容失败（已跳过）{f.original_name}：{e}")
                continue
            if not content or not content.strip():
                continue
            name_bonus = sum(max(1, len(k) // 2) for k in kw_lower if k in f.original_name.lower()) * 3
            for ci, ch in enumerate(_chunk_text(content)):
                chl = ch.lower()
                score = 0.0
                snippet = ""
                for k in kw_lower:
                    cnt = chl.count(k)
                    if cnt > 0:
                        score += cnt * max(1.0, len(k) / 2)   # 长关键词权重更高，命中更精准
                        if not snippet:
                            snippet = _make_snippet(ch, k)
                if score > 0:
                    scored_chunks.append((score + name_bonus, f, p, ci, ch, snippet))

        if not scored_chunks:
            return [], []

        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        # 3) 选择：先每个文件保底取 1 块（保证所有相关文件都有代表，避免少数高分文件霸占名额），
        #    再按分数填充剩余名额（单文件设上限）
        chosen = []
        file_taken = {}
        chosen_keys = set()  # (file_id, chunk_index)，避免同一块重复入选

        for score, f, p, ci, ch, snippet in scored_chunks:
            if f.id in file_taken:
                continue
            chosen.append((f, p, ci, ch, snippet))
            file_taken[f.id] = 1
            chosen_keys.add((f.id, ci))
            if len(chosen) >= max_chunks:
                break

        if len(chosen) < max_chunks:
            for score, f, p, ci, ch, snippet in scored_chunks:
                if (f.id, ci) in chosen_keys:
                    continue
                if file_taken.get(f.id, 0) >= PER_FILE_CHUNKS:
                    continue
                chosen.append((f, p, ci, ch, snippet))
                file_taken[f.id] = file_taken.get(f.id, 0) + 1
                chosen_keys.add((f.id, ci))
                if len(chosen) >= max_chunks:
                    break

        chunks_out = []
        ref_map = {}
        for f, p, ci, ch, snippet in chosen:
            label = f"{f.original_name}（项目：{p.name}"
            if f.doc_type:
                label += f"，{f.doc_type}"
            label += f"，第{ci + 1}段）"
            chunks_out.append({
                "label": label,
                "text": ch,
                "file_id": f.id,
                "original_name": f.original_name,
                "snippet": snippet,
            })
            ref_map.setdefault(f.id, {
                "file_id": f.id,
                "original_name": f.original_name,
                "project_id": p.id,
                "project_name": p.name,
                "project_code": p.code,
                "category": f.category or "其他",
                "doc_type": f.doc_type or "",
                "stage": f.stage or "",
                "download_url": f"/files/{f.id}/download",
                "snippet": snippet,
            })

        return chunks_out, list(ref_map.values())
    finally:
        db.close()


def _retrieve_achievement_context(question: str, top_k: int = 5, project_id: Optional[int] = None):
    """成果台账检索：检索成果的结构化字段（名称/类型/状态/权利人/申请日期/取得日期/备注）。
    成果是平台级知识资产，跨项目共享，因此不按 project_id 过滤——未关联项目（project_id 为空）
    的成果（如大量专利）同样参与检索并标注「平台级」。
    说明：仅检索结构化元数据（毫秒级、无 OCR）。证书全文检索需缓存/索引避免每次问答全量 OCR，留待后续。"""
    question = (question or "").strip()
    if not question:
        return [], []

    keywords = []
    try:
        keywords = _expand_query(question)
    except Exception as e:
        logger.warning(f"[成果检索] 关键词扩展失败，退回拆词：{e}")
    if not keywords:
        keywords = _fallback_terms(question)
    kw_lower = [k.lower() for k in keywords if k]

    max_chunks = MAX_CHUNKS
    if top_k and top_k > 0:
        max_chunks = max(1, min(top_k, MAX_CHUNKS))

    db = SessionLocal()
    try:
        rows = db.query(Achievement).all()
        pmap = {p.id: p.name for p in db.query(Project).all()}

        q_low = question.lower()
        scored = []  # (score, achievement, meta_text, snippet)
        for a in rows:
            meta_parts = []
            for label, val in [
                ("成果名称", a.name), ("类型", a.category), ("状态", a.status),
                ("权利人", a.holder), ("申请日期", a.application_date),
                ("取得日期", a.achieve_date), ("备注", a.remark),
            ]:
                if val:
                    meta_parts.append(f"{label}：{val}")
            meta_text = "；".join(meta_parts)
            if not meta_text.strip():
                continue

            name_low = (a.name or "").strip().lower()

            # 名称精确/包含匹配：成果名与问题互为子串时大幅加成（最精确的锚点，
            # 避免通用词「专利/申请/日期」让所有专利同分而淹没真正命中的那条）
            exact_bonus = 0.0
            if name_low and (name_low in q_low or q_low in name_low):
                exact_bonus += 100.0

            meta_low = meta_text.lower()
            score = 0.0
            snippet = ""
            for k in kw_lower:
                cnt = meta_low.count(k)
                if cnt > 0:
                    score += cnt * max(1.0, len(k) / 2)
                    if not snippet:
                        snippet = _make_snippet(meta_text, k)
            # 成果名关键词命中加成（名称是重要检索锚点）
            name_bonus = sum(max(1, len(k) // 2) for k in kw_lower if k in name_low) * 3

            if score <= 0 and exact_bonus <= 0:
                continue
            scored.append((score + name_bonus + exact_bonus, a, meta_text, snippet))

        if not scored:
            return [], []

        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = scored[:max_chunks]

        chunks_out = []
        ref_map = {}
        for score, a, text, snippet in chosen:
            pname = pmap.get(a.project_id, "") if a.project_id else ""
            scope = pname if pname else "平台级（未关联项目）"
            disp_name = a.name or a.file_name or "成果"
            chunks_out.append({
                "label": f"{disp_name}（{scope}，成果台账）",
                "text": text,
                "file_id": f"ach-{a.id}",
                "original_name": disp_name,
                "snippet": snippet,
            })
            ref_map.setdefault(a.id, {
                "file_id": f"ach-{a.id}",
                "original_name": disp_name,
                "project_id": a.project_id,
                "project_name": pname,
                "project_code": "",
                "category": a.category or "其他",
                "doc_type": "成果",
                "stage": "",
                "download_url": f"/achievements/{a.id}/download" if a.file_path else "",
                "snippet": snippet,
            })

        return chunks_out, list(ref_map.values())
    finally:
        db.close()


app = FastAPI(
    title="研发知识智能管理平台",
    description="MVP 第一版：项目管理 + 文件管理 + 内容解析",
    version="0.2.0",
)


# ============ 认证中间件 ============
PUBLIC_PATHS = {"/", "/auth/login", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
        return await call_next(request)

    # 1) 内部服务 token（QQ 机器人），走 X-Bot-Token 头
    bot_token = request.headers.get("X-Bot-Token", "")
    if security.BOT_TOKEN and bot_token and hmac.compare_digest(bot_token, security.BOT_TOKEN):
        username = "bot"
    else:
        # 2) 用户 token：Authorization 头 或 ?token= 查询参数（供下载链接使用）
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token:
            token = request.query_params.get("token", "")
        username = security._verify_token(token)
        if not username:
            return JSONResponse(status_code=401, content={"detail": "未授权，请先登录"})

    # 记录审计日志（失败不阻断）
    ip = request.client.host if request.client else ""
    _audit(username, f"{request.method} {path}", ip=ip)

    # 供 /auth/refresh 等下游接口读取当前用户
    request.state.username = username
    return await call_next(request)


# ============ 路由挂载 ============
from routers.auth import router as auth_router

app.include_router(auth_router)


# ============ 项目 CRUD ============
from pydantic import BaseModel

class ProjectCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    stage: Optional[str] = "项目立项"

class AskRequest(BaseModel):
    question: str
    top_k: int = 5
    project_id: Optional[int] = None   # 新增：None 表示搜全部项目


class CrossProjectRequest(BaseModel):
    question: str
    category: Optional[str] = None   # 可选：显式指定大类（资料/成果/合同/附件），覆盖 LLM 判断
    doc_type: Optional[str] = None   # 可选：显式指定资料/成果类型（如"科技项目申请书"），覆盖 LLM 判断


class DownloadRequest(BaseModel):
    file_ids: List[int]   # 要打包下载的文件 id 列表


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    stage: Optional[str] = None


@app.post("/projects", summary="创建项目")
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    existing = db.query(Project).filter(Project.code == project.code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"项目编号 {project.code} 已存在")

    stage = project.stage if project.stage in PROJECT_STAGES else "项目立项"
    db_project = Project(
        code=project.code,
        name=project.name,
        description=project.description,
        stage=stage,
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


@app.get("/projects", summary="获取项目列表")
def list_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.created_at.desc()).all()


@app.get("/projects/{project_id}", summary="获取单个项目详情")
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@app.put("/projects/{project_id}", summary="更新项目")
def update_project(project_id: int, project: ProjectUpdate, db: Session = Depends(get_db)):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.name is not None:
        db_project.name = project.name
    if project.description is not None:
        db_project.description = project.description
    if project.stage is not None:
        db_project.stage = project.stage if project.stage in PROJECT_STAGES else "项目立项"

    db.commit()
    db.refresh(db_project)
    return db_project


@app.get("/projects/{project_id}/dashboard", summary="项目管理聚合：信息卡 + 6 指标 + 4 阶段 + 资料分组")
def project_dashboard(project_id: int, db: Session = Depends(get_db)):
    """单项目一屏聚合：文件数/合同数/合同总金额/经费投入/成果数/待办数 + 四阶段资料数 + 资料按阶段分组。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    files = db.query(ProjectFile).filter(ProjectFile.project_id == project_id).all()
    contracts = db.query(Contract).filter(Contract.project_id == project_id).all()
    fundings = db.query(Funding).filter(Funding.project_id == project_id).all()
    achievements = db.query(Achievement).filter(Achievement.project_id == project_id).all()

    file_count = len(files)
    contract_count = len(contracts)
    contract_total_amount = round(sum((c.amount_incl_tax or c.amount_value or 0) for c in contracts), 2)
    funding_income = round(sum(f.amount or 0 for f in fundings if f.fund_type == "到账"), 2)
    achievement_count = len(achievements)
    todo_count = sum(1 for it in _generate_todo_items(db) if it.get("project_id") == project_id)

    # 四阶段资料数（缺失/空阶段归「未划分」，不占用四阶段计数）
    stage_counts = {s: 0 for s in PROJECT_STAGES}
    unclassified = 0
    files_by_stage_map: dict = {}
    for f in files:
        key = f.stage if f.stage in PROJECT_STAGES else "未划分"
        if key == "未划分":
            unclassified += 1
        else:
            stage_counts[key] += 1
        files_by_stage_map.setdefault(key, []).append({
            "id": f.id,
            "original_name": f.original_name,
            "category": f.category or "",
            "doc_type": f.doc_type or "",
            "ai_summary": f.ai_summary or "",
            "processing_status": f.processing_status or "done",
            "uploaded_at": f.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if f.uploaded_at else "",
        })

    stages = [{"stage": s, "file_count": stage_counts[s]} for s in PROJECT_STAGES]
    files_by_stage = [{"stage": s, "files": files_by_stage_map.get(s, [])} for s in PROJECT_STAGES]
    if unclassified > 0:
        files_by_stage.append({"stage": "未划分", "files": files_by_stage_map.get("未划分", [])})

    return {
        "project": {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "description": project.description or "",
            "stage": project.stage or "项目立项",
            "created_at": project.created_at.strftime("%Y-%m-%d %H:%M:%S") if project.created_at else "",
        },
        "metrics": {
            "file_count": file_count,
            "contract_count": contract_count,
            "contract_total_amount": contract_total_amount,
            "funding_income": funding_income,
            "achievement_count": achievement_count,
            "todo_count": todo_count,
        },
        "stages": stages,
        "files_by_stage": files_by_stage,
    }


@app.delete("/projects/{project_id}", summary="删除项目")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    for f in db_project.files:
        # 删除文件本体
        if os.path.exists(f.file_path):
            os.remove(f.file_path)
        # 删除对应的提取文本
        cpath = _content_path(f.stored_name)
        if os.path.exists(cpath):
            os.remove(cpath)


    db.delete(db_project)
    db.commit()
    return {"message": f"项目 {db_project.name} 已删除"}


# ============ 文件上传/下载 ============
@app.post("/projects/{project_id}/files", summary="上传文件到项目")
async def upload_file(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    ext = os.path.splitext(file.filename)[1]
    stored_name = f"{timestamp}{ext}"
    file_path = os.path.join(UPLOAD_DIR, stored_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = os.path.getsize(file_path)

    db_file = ProjectFile(
        project_id=project_id,
        original_name=file.filename,
        stored_name=stored_name,
        file_path=file_path,
        size=file_size,
        content=None,
        processing_status="done",
    )
    db.add(db_file)
    db.commit()
    db.refresh(db_file)

    # ---- 不支持解析的类型（如 .gw）：只存文件，标记为附件，跳过解析与分类 ----
    if not is_supported_file(file.filename):
        db_file.category = "附件"
        db_file.ai_summary = "（该文件类型暂不支持内容解析，已作为附件保存）"
        db_file.processing_status = "done"
        db.commit()
        return db_file

    # ---- 纯扫描版 PDF 走后台 OCR：先入库立即返回，OCR + 分类在后台完成后自动回填 ----
    if ext.lower() == ".pdf" and _pdf_needs_ocr(file_path):
        db_file.processing_status = "processing"
        db.commit()
        _ocr_executor.submit(_process_file_async, db_file.id, project_id, file_path, file.filename, stored_name)
        return db_file

    # ---- 同步路径：非扫描件直接提取文本 → 智能分类 ----
    try:
        content = extract_text_from_file(file_path)
        _save_content(stored_name, content)
    except Exception as e:
        logger.error(f"[上传] 提取内容失败：{e}")
        content = ""

    try:
        text = _load_content(db_file)
        if text and text.strip():
            cls = _classify_file(text, file.filename)
            if cls:
                _apply_classification(db, db_file, cls, project_id)
        db.commit()
    except Exception as e:
        logger.error(f"[上传] 智能分类失败：{e}")
        db.rollback()

    db_file.processing_status = "done"
    db.commit()
    return db_file


def _file_to_dict(f: ProjectFile) -> dict:
    """把项目文件记录转成前端可读 dict（含财务单据金额/日期字段）。"""
    return {
        "id": f.id,
        "project_id": f.project_id,
        "original_name": f.original_name,
        "stored_name": f.stored_name,
        "size": f.size or 0,
        "category": f.category or "",
        "doc_type": f.doc_type or "",
        "stage": f.stage or "",
        "ai_summary": f.ai_summary or "",
        "amount": f.amount or 0,
        "doc_date": f.doc_date or "",
        "processing_status": f.processing_status or "done",
        "uploaded_at": f.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if f.uploaded_at else "",
    }


@app.get("/projects/{project_id}/files", summary="获取项目文件列表（可按分类/阶段筛选）")
def list_files(
    project_id: int,
    category: Optional[str] = None,
    stage: Optional[str] = None,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    q = db.query(ProjectFile).filter(ProjectFile.project_id == project_id)
    if category:
        q = q.filter(ProjectFile.category == category)
    if stage:
        q = q.filter(ProjectFile.stage == stage)
    return [_file_to_dict(f) for f in q.all()]


@app.get("/files/{file_id}/download", summary="下载文件")
def download_file(file_id: int, db: Session = Depends(get_db)):
    db_file = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="文件不存在")

    if not os.path.exists(db_file.file_path):
        raise HTTPException(status_code=404, detail="文件在磁盘上不存在")

    return FileResponse(
        path=db_file.file_path,
        filename=db_file.original_name,
        media_type="application/octet-stream",
    )


@app.post("/projects/{project_id}/download-zip", summary="批量下载文件（打包成 zip）")
def download_files_zip(project_id: int, req: DownloadRequest, db: Session = Depends(get_db)):
    """把指定 file_ids 的文件打包成一个 zip 返回（支持一次性下载多个/全部文件）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not req.file_ids:
        raise HTTPException(status_code=400, detail="未选择要下载的文件")

    files = (
        db.query(ProjectFile)
        .filter(ProjectFile.project_id == project_id, ProjectFile.id.in_(req.file_ids))
        .all()
    )
    if not files:
        raise HTTPException(status_code=404, detail="未找到要下载的文件")

    buf = io.BytesIO()
    used_names = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if not os.path.exists(f.file_path):
                continue
            # 处理重名文件：同名时自动加序号，避免 zip 内相互覆盖
            name = f.original_name or f.stored_name
            if name in used_names:
                used_names[name] += 1
                base, ext = os.path.splitext(name)
                name = f"{base}_{used_names[name]}{ext}"
            else:
                used_names[name] = 0
            zf.write(f.file_path, arcname=name)
    buf.seek(0)

    zip_name = f"{project.name or '项目'}_文件打包.zip"
    disposition = f"attachment; filename*=UTF-8''{quote(zip_name)}"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


@app.delete("/files/{file_id}", summary="删除单个文件")
def delete_file(file_id: int, db: Session = Depends(get_db)):
    db_file = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 删除文件本体
    if os.path.exists(db_file.file_path):
        os.remove(db_file.file_path)

    # 删除对应的提取文本
    cpath = _content_path(db_file.stored_name)
    if os.path.exists(cpath):
        os.remove(cpath)

    db.delete(db_file)
    db.commit()
    return {"message": f"文件 {db_file.original_name} 已删除"}


# ============ 文件内容解析 ============
@app.get("/files/{file_id}/content", summary="获取文件文本内容")
def get_file_content(file_id: int, db: Session = Depends(get_db)):
    """提取并返回文件的文本内容"""
    db_file = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="文件不存在")

    if not os.path.exists(db_file.file_path):
        raise HTTPException(status_code=404, detail="文件在磁盘上不存在")

    content = _load_content(db_file)

    #content = db_file.content

    # 旧文件还没存过内容（或内容为空）时，临时提取一次并回存，之后预览就是秒开
   # if content is None or not content.strip():
     #   content = extract_text_from_file(db_file.file_path)
    #    try:
     #       db_file.content = content
     #       db.commit()
     #   except Exception as e:
    #        print(f"[预览] 回存内容失败：{e}")

    return {
        "id": db_file.id,
        "original_name": db_file.original_name,
        "content": content or "",
    }


@app.get("/files/{file_id}/status", summary="查询文件解析状态（后台 OCR 进度）")
def get_file_status(file_id: int, db: Session = Depends(get_db)):
    db_file = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {
        "file_id": db_file.id,
        "original_name": db_file.original_name,
        "processing_status": db_file.processing_status or "done",
        "category": db_file.category or "",
        "doc_type": db_file.doc_type or "",
        "has_content": bool(db_file.content and db_file.content.strip())
                       or os.path.exists(_content_path(db_file.stored_name)),
    }

# ============ 全文搜索 ============
def _make_snippet(content: str, keyword: str, radius: int = 40) -> str:
    """截取关键词附近的一小段文字，用于搜索结果预览"""
    if not content:
        return ""
    idx = content.lower().find(keyword.lower())
    if idx == -1:
        idx = 0
    start = max(0, idx - radius)
    end = min(len(content), idx + len(keyword) + radius)
    snippet = content[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


@app.get("/search", summary="全文搜索")
def search_files(q: str, db: Session = Depends(get_db)):
    """在文件内容中搜索关键词，返回匹配的文件及命中片段"""
    keyword = q.strip()
    if not keyword:
        return []

    rows = (
        db.query(ProjectFile, Project)
        .join(Project, ProjectFile.project_id == Project.id)
        .all()
    )

    results = []
    for f, p in rows:
        content = _load_content(f)
        if not content:
            continue
        if keyword.lower() not in content.lower():
            continue
        results.append({
            "file_id": f.id,
            "original_name": f.original_name,
            "project_id": p.id,
            "project_name": p.name,
            "project_code": p.code,
            "snippet": _make_snippet(content, keyword),
        })
    return results


# ============ 健康检查 ============
# ============ 智能问答接口 ============
@app.post("/ask", summary="智能问答")
def ask_question(req: AskRequest):
    """基于知识库内容的智能问答（RAG）：分块检索 + 单次综合回答。
    通过分块把大文件的任意相关段落都纳入，避免旧版只读文件开头导致回答不全面。"""
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 两路检索：项目资料 + 成果台账；各取一半额度，合并后总量仍受 MAX_CHUNKS 约束
    total = max(1, min(req.top_k if req.top_k else MAX_CHUNKS, MAX_CHUNKS))
    half = max(1, total // 2)
    chunks, references = _retrieve_context(question, half, project_id=req.project_id)
    ach_chunks, ach_refs = _retrieve_achievement_context(question, half, project_id=req.project_id)
    chunks = chunks + ach_chunks
    references = references + ach_refs

    if not chunks:
        return {
            "answer": "资料中没有找到相关内容。请尝试更换关键词，或先上传相关文档。",
            "references": [],
        }

    context = "\n\n".join(f"【{c['label']}】\n{c['text']}" for c in chunks)
    logger.info(f"[问答] 命中 {len(chunks)} 个相关片段 / 共 {len(context)} 字，开始生成回答")
    try:
        answer = _call_deepseek(question, context)
    except Exception as e:
        print(f"[问答] 调用 DeepSeek 失败：{e}")
        raise HTTPException(status_code=500, detail=f"调用 DeepSeek 失败：{e}")

    return {"answer": answer, "references": references}

@app.get("/", summary="健康检查")
def health_check():
    return {"status": "ok", "message": "研发知识智能管理平台运行中"}

# ============ 大模型文档摘要 ============
def _call_deepseek_summary(content: str) -> str:
    """调用 DeepSeek 生成 50~100 字文档摘要"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    prompt = (
        "请用 50 到 100 个字，概括下面这份文档的主要内容。"
        "直接输出总结，不要加多余的开头和解释：\n\n" + content
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


@app.post("/files/{file_id}/summary", summary="生成文件摘要")
def summarize_file(file_id: int, db: Session = Depends(get_db)):
    """用大模型生成文件 50~100 字摘要"""
    db_file = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="文件不存在")

    content = _load_content(db_file)
    if not content or not content.strip():
        raise HTTPException(status_code=404, detail="文件暂无文本内容")

    content = content[:4000]  # 截断到约 4000 字符，避免过长

    try:
        summary = _call_deepseek_summary(content)
    except Exception as e:
        print(f"[摘要] 调用 DeepSeek 失败：{e}")
        raise HTTPException(status_code=500, detail=f"调用 DeepSeek 失败：{e}")

    return {"summary": summary}

# ============ 合同统计：上传时提取 + 存库查询模式 ============
def _extract_contract_fields(content: str) -> dict:
    """从单份合同文本中提取关键字段，返回英文键的 dict（金额已归一化为纯数字）。"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    # 取头部 6000 字 + 尾部 2000 字：合同关键信息（编号/金额/日期/甲乙方）常在首尾
    text_part = content[:6000] + "\n……（中间省略）……\n" + content[-2000:]
    prompt = (
        "请从下面的合同文本中提取关键信息，只输出一个 JSON 对象，不要输出任何其他文字或解释。\n"
        "字段固定为：\n"
        "{\n"
        "  \"contract_name\": \"合同名称\",\n"
        "  \"contract_no\": \"合同编号\",\n"
        "  \"party_a\": \"甲方\",\n"
        "  \"party_b\": \"乙方\",\n"
        "  \"amount_value\": 0,\n"
        "  \"currency\": \"币种\",\n"
        "  \"service_content\": \"服务内容一句话概括\",\n"
        "  \"sign_date\": \"签署日期(YYYY-MM-DD)\",\n"
        "  \"term\": \"合同期限(如:一年)\",\n"
        "  \"contract_type\": \"合同类型(如:采购合同)\",\n"
        "  \"service_period\": \"服务周期(如:2024-01-01至2025-12-31)\",\n"
        "  \"is_contract\": true\n"
        "}\n\n"

        "严格规则：\n"
        "0. is_contract 表示这份文件是不是合同/协议：真正的合同、协议、采购单、订单填 true；工程方案、技术报告、设计文档、说明书等非合同文件填 false。\n"
        "1. amount_value 必须是纯数字，去掉\"元/万元/万元整/货币符号/逗号/中文大写\"。"
        "例：\"壹佰万元整\"→1000000，\"100万\"→1000000，\"1,000,000.00元\"→1000000。"
        "注意：原文写\"壹佰万元\"金额就是 1000000，不要写成 100。\n"
        "2. 没有的字段填空字符串 \"\"，金额没有就填 0。\n"
        "3. 只输出 JSON，禁止输出 markdown 代码块，禁止解释。\n\n"
        "合同文本：\n" + text_part
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1000,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    # 兜底：把金额强行转成数字
    try:
        data["amount_value"] = float(data.get("amount_value") or 0)
    except Exception:
        data["amount_value"] = 0.0
    # 非合同文件（工程方案、技术文档等）不写摘要库：返回空 dict，调用方会跳过
    if data.get("is_contract") is False:
        return {}
    return data


def _backfill_contract_summaries(project_id: int, db) -> int:
    """重建该项目所有文件的合同摘要：先清空旧记录，再逐份重新提取（自动跳过非合同文件）"""
    # 1) 先删掉该项目下所有旧摘要，清掉以前误提取的非合同文件/错误数据
    db.query(ContractSummary).filter(ContractSummary.project_id == project_id).delete()
    db.commit()

    # 2) 逐份重新提取
    files = db.query(ProjectFile).filter(ProjectFile.project_id == project_id).all()
    done = 0
    for f in files:
        content = _load_content(f)
        if not content or not content.strip():
            continue
        try:
            fields = _extract_contract_fields(content)
        except Exception as e:
            print(f"[补提取] {f.original_name} 提取失败：{e}")
            continue
        if not fields:
            continue
        db.add(ContractSummary(
            file_id=f.id,
            project_id=project_id,
            contract_name=fields.get("contract_name", ""),
            contract_no=fields.get("contract_no", ""),
            party_a=fields.get("party_a", ""),
            party_b=fields.get("party_b", ""),
            amount_value=fields.get("amount_value") or 0,
            currency=fields.get("currency", ""),
            service_content=fields.get("service_content", ""),
            sign_date=fields.get("sign_date", ""),
            term=fields.get("term", ""),
            contract_type=fields.get("contract_type", ""),
            service_period=fields.get("service_period", ""),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        done += 1
    db.commit()
    return done

def _backfill_document_summaries(project_id: int, db) -> int:
    """重建该项目所有项目资料摘要：先清空旧记录，再逐份提取（自动跳过合同文件）"""
    db.query(DocumentSummary).filter(DocumentSummary.project_id == project_id).delete()
    db.commit()

    files = db.query(ProjectFile).filter(ProjectFile.project_id == project_id).all()
    done = 0
    for f in files:
        content = _load_content(f)
        if not content or not content.strip():
            continue
        try:
            doc = _extract_document_summary(content)
        except Exception as e:
            print(f"[补提取资料] {f.original_name} 提取失败：{e}")
            continue
        if not doc:
            continue
        db.add(DocumentSummary(
            file_id=f.id,
            project_id=project_id,
            doc_type=doc.get("doc_type", ""),
            summary=doc.get("summary", ""),
            stage=doc.get("stage", ""),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        done += 1
    db.commit()
    return done

def _normalize_stage(stage, doc_type=""):
    """优先按 doc_type 精确归阶段（16 类有固定归属），识别不出时再按 AI 给的 stage 兜底"""
    dt = (doc_type or "").strip()

    # 一、按 doc_type 关键词归阶段（最可靠，16 类全覆盖）
    if any(k in dt for k in ["可研", "经济分析"]):
        return "项目投决"
    if any(k in dt for k in ["任务书", "进展", "延期"]):
        return "项目实施"
    if any(k in dt for k in ["验收", "总结报告", "决算", "成长", "培养"]):
        return "项目验收"
    if any(k in dt for k in ["申请", "方案", "承诺", "审查", "汇总表","纪要"]):
        return "项目立项"

    # 二、doc_type 识别不出时，按 AI 给的 stage 兜底
    s = (stage or "").strip()
    if "投决" in s or "投资" in s:
        return "项目投决"
    if "实施" in s or "执行" in s:
        return "项目实施"
    if "验收" in s:
        return "项目验收"
    if "立项" in s:
        return "项目立项"

    return "项目立项"

def _extract_document_summary(content: str) -> dict:
    """从单份项目资料中提取文档类型 + 一句话摘要，返回英文键 dict；合同文件返回空 {}（交给合同库）"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    text_part = content[:6000]
    prompt = (
    "请阅读下面这份项目材料，判断：\n"
    "1. 它是不是合同/协议类文件（is_material：合同协议返回 false，其余 true）\n"
    "2. 它属于哪一类项目资料（doc_type，从下面 16 类里选最贴切的一个）\n"
    "3. 它属于项目的哪个阶段（stage，四选一：项目立项 / 项目投决 / 项目实施 / 项目验收）\n"
    "4. 写一段 50-100 字的摘要（summary）\n\n"
    "16 类资料：\n"
    "年度科技项目申请汇总表、科技项目申请书、技术方案、技术方案审查意见、"
    "可研报告、可研审查意见、责任承诺书、任务书、验收申请表、执行情况总结报告、"
    "经费决算表、经济分析报告、成员培养成长情况总结、验收意见、进展报表、延期申请表\n\n"
    "阶段说明（每类资料只属于一个阶段，请严格按下面对应）：\n"
    "- 项目立项：年度科技项目申请汇总表、科技项目申请书、技术方案、技术方案审查意见、责任承诺书\n"
    "- 项目投决：可研报告、可研审查意见、经济分析报告\n"
    "- 项目实施：任务书、进展报表、延期申请表\n"
    "- 项目验收：验收申请表、执行情况总结报告、经费决算表、成员培养成长情况总结、验收意见\n\n"
    "只返回 JSON，不要任何其他文字：\n"
    '{"is_material": true/false, "doc_type": "...", "stage": "项目立项", "summary": "..."}'
)

    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 600,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    # 合同文件不进资料库
    if data.get("is_material") is False:
        return {}
    data["stage"] = _normalize_stage(data.get("stage"), data.get("doc_type", ""))
    return data


# ============ 合同格式白名单 + 财务单据标签（确定性识别，不依赖 LLM）============
# 合同只可能是 Word 或 PDF，其余格式（图片/Excel/文本等）一律不是合同。
CONTRACT_EXTS = {".doc", ".docx", ".pdf"}


def _file_ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _is_contract_ext(filename: str) -> bool:
    """判断文件扩展名是否可能为合同（仅 .doc/.docx/.pdf）。"""
    return _file_ext(filename) in CONTRACT_EXTS


# 财务单据标签（有金额的单据）：上传后自动归集到「财务单据」大类
FINANCIAL_DOC_TAGS = ["发票", "结算单", "暂估单", "估算单", "付款凭证", "报销单", "收据"]

_FIN_TAG_KEYWORDS = {
    "发票": ["发票", "增值税", "invoice", "fapiao"],
    "结算单": ["结算单", "结算表", "工程结算", "竣工结算", "结算书", "settlement"],
    "暂估单": ["暂估单", "暂估", "预估单", "暂估金额"],
    "估算单": ["估算单", "估算表", "概算", "估算书"],
    "付款凭证": ["付款凭证", "付款申请", "支付凭证", "付款单"],
    "报销单": ["报销单", "费用报销"],
    "收据": ["收据", "receipt"],
}


def _detect_financial_tag(filename: str, content: str = "") -> str:
    """根据文件名 + 正文关键词确定性识别财务单据类型，返回标签名或空串。"""
    name = (filename or "").lower()
    text = (content or "").lower()
    for tag, kws in _FIN_TAG_KEYWORDS.items():
        for kw in kws:
            if kw in name or kw in text:
                return tag
    return ""


# ============ 智能分类：一次 LLM 调用完成大类判断 + 资料分类 + 摘要 + 合同字段 ============
def _classify_file(content: str, filename: str = "") -> dict:
    """一次调用 DeepSeek，完成：文件大类（合同/资料/成果/附件）+ 资料分类或成果子类型 + 阶段 + 摘要 +（合同字段）。
    返回统一 dict：{category, summary, doc_type, stage, contract}；失败返回空 dict {}。"""
    if not content or not content.strip():
        return {}
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    text_part = content[:6000] + "\n……（中间省略）……\n" + content[-2000:]
    prompt = (
        "请阅读下面这份文件的内容，先判断它属于哪一类，再按类别提取信息。\n\n"
        "【文件大类】五选一：\n"
        "- \"合同\"：合同、协议（注意：只有 .doc/.docx/.pdf 格式的文件才可能是合同）\n"
        "- \"财务单据\"：发票、结算单、暂估单、估算单、付款凭证、报销单、收据等有金额的单据\n"
        "- \"资料\"：科技项目的方案、报告、申请书、任务书、纪要、验收材料、表格等\n"
        "- \"成果\"：专利证书（发明/实用新型/外观）、软件著作权、论文、获奖证书、科技奖励、标准、成果登记证书、鉴定报告等\n"
        "- \"附件\"：无法归入上述四类的其他文件（图纸、照片、说明等）\n\n"
        "只返回一个 JSON 对象，格式：\n"
        "{\n"
        "  \"category\": \"合同/财务单据/资料/成果/附件\",\n"
        "  \"summary\": \"50-100字概括这份文件\",\n"
        "  \"doc_type\": \"\",\n"
        "  \"stage\": \"\",\n"
        "  \"amount\": 0,\n"
        "  \"doc_date\": \"\",\n"
        "  \"contract\": null\n"
        "}\n\n"
        "【category=财务单据 时】doc_type 从下面选最贴切的一个：发票、结算单、暂估单、估算单、付款凭证、报销单、收据；"
        "amount 填价税合计金额（纯数字），doc_date 填单据发生日期（YYYY-MM-DD，没有填空串）\n\n"
        "【category=资料 时】doc_type 从下面 16 类里选最贴切的一个，stage 严格按下面对应：\n"
        "年度科技项目申请汇总表、科技项目申请书、技术方案、技术方案审查意见、可研报告、可研审查意见、"
        "责任承诺书、任务书、验收申请表、执行情况总结报告、经费决算表、经济分析报告、成员培养成长情况总结、"
        "验收意见、进展报表、延期申请表\n"
        "- 项目立项：年度科技项目申请汇总表、科技项目申请书、技术方案、技术方案审查意见、责任承诺书\n"
        "- 项目投决：可研报告、可研审查意见、经济分析报告\n"
        "- 项目实施：任务书、进展报表、延期申请表\n"
        "- 项目验收：验收申请表、执行情况总结报告、经费决算表、成员培养成长情况总结、验收意见\n"
        "（category 不是资料时 stage 填空串）\n\n"
        "【category=成果 时】doc_type 从下面选最贴切的一个：专利、论文、软件著作权、获奖、标准、成果登记、鉴定报告\n\n"
        "【category=合同 时】contract 填下面的对象（否则 null）：\n"
        "{\"contract_name\":\"合同名称\",\"contract_no\":\"合同编号\",\"party_a\":\"甲方\",\"party_b\":\"乙方\","
        "\"amount_value\":0,\"currency\":\"币种\",\"service_content\":\"服务内容一句话\","
        "\"sign_date\":\"签署日期\",\"term\":\"期限\",\"contract_type\":\"合同类型\",\"service_period\":\"服务周期\"}\n\n"
        "严格规则：\n"
        "1. amount_value 与 amount 必须是纯数字（去掉元/万元/货币符号/逗号/中文大写），\"壹佰万元整\"→1000000，\"100万\"→1000000\n"
        "2. 没有的字段填空串\"\"，金额没有填 0\n"
        "3. 只有 .doc/.docx/.pdf 格式的文件才可归为「合同」；发票/结算单/暂估单/估算单等有金额单据一律归「财务单据」，不能归「合同」或「资料」\n"
        "4. 只输出 JSON，禁止 markdown 代码块和任何解释\n\n"
        f"文件名：{filename}\n"
        f"文件内容：\n{text_part}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}

    cat = str(data.get("category", "")).strip()
    if "合同" in cat:
        category = "合同"
    elif "财务" in cat or "单据" in cat or "发票" in cat or "结算" in cat or "暂估" in cat or "估算" in cat:
        category = "财务单据"
    elif "成果" in cat:
        category = "成果"
    elif "资料" in cat:
        category = "资料"
    else:
        category = "附件"

    # 合同格式硬约束：只有 Word/PDF 才可能是合同；其余格式即使 LLM 判为合同也强制纠正为财务单据/附件
    if category == "合同" and not _is_contract_ext(filename):
        tag = _detect_financial_tag(filename, content)
        category = "财务单据" if tag else "附件"

    summary = (data.get("summary") or "").strip()
    result = {
        "category": category,
        "summary": summary,
        "doc_type": "",
        "stage": "",
        "contract": None,
        "amount": 0.0,
        "doc_date": "",
    }
    if category == "资料":
        result["doc_type"] = (data.get("doc_type") or "").strip()
        result["stage"] = _normalize_stage(data.get("stage"), result["doc_type"])
    elif category == "成果":
        result["doc_type"] = (data.get("doc_type") or "").strip()
    elif category == "合同":
        c = data.get("contract") or {}
        try:
            c["amount_value"] = float(c.get("amount_value") or 0)
        except Exception:
            c["amount_value"] = 0.0
        result["contract"] = c
    elif category == "财务单据":
        result["doc_type"] = _detect_financial_tag(filename, content) or (data.get("doc_type") or "").strip() or "其他财务单据"
        amt = data.get("amount")
        if not amt:
            amt = (data.get("contract") or {}).get("amount_value")
        try:
            result["amount"] = float(amt or 0)
        except Exception:
            result["amount"] = 0.0
        result["doc_date"] = str(data.get("doc_date") or "").strip()
    return result


def _apply_classification(db, f, cls: dict, project_id: int):
    """把一次分类结果写回文件记录，并按类别写入对应摘要表（资料/合同/成果/财务单据）。"""
    f.category = cls["category"]
    f.ai_summary = cls["summary"]
    f.doc_type = ""
    f.stage = ""
    f.amount = None
    f.doc_date = None
    if cls["category"] == "资料":
        f.doc_type = cls["doc_type"]
        f.stage = cls["stage"]
        db.add(DocumentSummary(
            file_id=f.id, project_id=project_id,
            doc_type=cls["doc_type"], summary=cls["summary"], stage=cls["stage"],
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
    elif cls["category"] == "成果":
        f.doc_type = cls.get("doc_type") or ""   # 专利/论文/软著/获奖/标准…
    elif cls["category"] == "财务单据":
        f.doc_type = cls.get("doc_type") or "其他财务单据"
        f.amount = cls.get("amount") or None
        f.doc_date = cls.get("doc_date") or None
        # 财务单据也写一条资料摘要，便于跨项目检索/统计引用
        db.add(DocumentSummary(
            file_id=f.id, project_id=project_id,
            doc_type=f.doc_type, summary=cls["summary"], stage="",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
    elif cls["category"] == "合同" and cls.get("contract"):
        c = cls["contract"]
        db.add(ContractSummary(
            file_id=f.id, project_id=project_id,
            contract_name=c.get("contract_name", ""), contract_no=c.get("contract_no", ""),
            party_a=c.get("party_a", ""), party_b=c.get("party_b", ""),
            amount_value=c.get("amount_value") or 0, currency=c.get("currency", ""),
            service_content=c.get("service_content", ""), sign_date=c.get("sign_date", ""),
            term=c.get("term", ""), contract_type=c.get("contract_type", ""),
            service_period=c.get("service_period", ""),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))


def _process_file_async(file_id: int, project_id: int, file_path: str, original_name: str, stored_name: str):
    """后台线程：提取全文（含扫描件 OCR）→ 写磁盘 → 智能分类 → 回填数据库，最后把状态置为 done/error。"""
    db = SessionLocal()
    try:
        try:
            content = extract_text_from_file(file_path)
            _save_content(stored_name, content)
        except Exception as e:
            logger.error(f"[后台] 提取内容失败 {original_name}：{e}")

        f = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
        if f is None:
            return
        try:
            text = _load_content(f)
            if text and text.strip():
                cls = _classify_file(text, original_name)
                if cls:
                    _apply_classification(db, f, cls, project_id)
            db.commit()
            # 新增：若分类为「合同」，自动导入到独立合同台账（幂等，按 source_file_id 去重）
            if f.category == "合同":
                try:
                    _import_contract_from_project_file(db, f.id)
                except Exception as e:
                    logger.error(f"[后台] 自动导入合同失败 {original_name}：{e}")
        except Exception as e:
            logger.error(f"[后台] 智能分类失败 {original_name}：{e}")
            db.rollback()

        f = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
        if f is not None:
            f.processing_status = "done"
            db.commit()
    except Exception as e:
        logger.error(f"[后台] 处理异常 {original_name}：{e}")
        try:
            f = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
            if f is not None:
                f.processing_status = "error"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _backfill_all(project_id: int, db) -> dict:
    """重建该项目所有文件的分类与摘要：先清空旧记录，再逐份用一次 LLM 调用重新分类。"""
    db.query(ContractSummary).filter(ContractSummary.project_id == project_id).delete()
    db.query(DocumentSummary).filter(DocumentSummary.project_id == project_id).delete()
    db.commit()

    files = db.query(ProjectFile).filter(ProjectFile.project_id == project_id).all()
    n_contracts = n_docs = n_attach = n_achievements = n_finance = 0
    for f in files:
        if not is_supported_file(f.original_name):
            f.category = "附件"
            f.ai_summary = "（该文件类型暂不支持内容解析，已作为附件保存）"
            n_attach += 1
            continue
        content = _load_content(f)
        if not content or not content.strip():
            f.category = "附件"
            f.ai_summary = "（未能提取到文字内容）"
            n_attach += 1
            continue
        try:
            cls = _classify_file(content, f.original_name)
        except Exception as e:
            logger.error(f"[补分类] {f.original_name} 失败：{e}")
            continue
        if not cls:
            continue
        _apply_classification(db, f, cls, project_id)
        if cls["category"] == "合同":
            try:
                _import_contract_from_project_file(db, f.id)
            except Exception as e:
                logger.error(f"[补分类] 自动导入合同失败 {f.original_name}：{e}")
            n_contracts += 1
        elif cls["category"] == "资料":
            n_docs += 1
        elif cls["category"] == "成果":
            n_achievements += 1
        elif cls["category"] == "财务单据":
            n_finance += 1
        else:
            n_attach += 1
    db.commit()
    return {"contracts": n_contracts, "documents": n_docs, "achievements": n_achievements,
            "attachments": n_attach, "financial_docs": n_finance}


@app.post("/projects/{project_id}/reindex", summary="补提取历史文件的分类和摘要")
def project_reindex(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    stats = _backfill_all(project_id, db)
    return {
        "message": f"补分类完成：合同 {stats['contracts']} 份，项目资料 {stats['documents']} 份，成果 {stats['achievements']} 份，财务单据 {stats['financial_docs']} 份，附件 {stats['attachments']} 份"
    }


@app.post("/admin/correct-classifications", summary="一次性修正现存文件分类（合同格式白名单 + 财务单据标签，确定性、不调 LLM）")
def correct_classifications(db: Session = Depends(get_db)):
    """对现存文件做确定性分类修正：
    1) 非 Word/PDF 却标为「合同」的文件 → 按文件名关键词改标「财务单据」或「附件」，并清掉其错误合同摘要；
    2) 文件名含发票/结算单/暂估单/估算单等关键词、当前为「附件/其他/未分类」的文件 → 改标「财务单据」。
    返回修正数量；合同台账里的非 Word/PDF 记录仅报告、不自动删除。"""
    fixed_contracts = 0
    tagged_finance = 0
    bad_contract_ledger = []
    removed_contracts = []

    files = db.query(ProjectFile).all()
    for f in files:
        # 1) 合同格式硬约束：非 Word/PDF 不能是合同
        if f.category == "合同" and not _is_contract_ext(f.original_name):
            tag = _detect_financial_tag(f.original_name)
            if tag:
                f.category = "财务单据"
                f.doc_type = tag
            else:
                f.category = "附件"
                f.doc_type = ""
            f.stage = ""
            db.query(ContractSummary).filter(ContractSummary.file_id == f.id).delete()
            fixed_contracts += 1
        # 2) 财务单据关键词识别：当前未归类（附件/其他/空）且文件名带财务关键词 → 打标签
        elif f.category in (None, "", "附件", "其他"):
            tag = _detect_financial_tag(f.original_name)
            if tag:
                f.category = "财务单据"
                f.doc_type = tag
                tagged_finance += 1
        # 已正确分类的资料/成果/合同/财务单据不改动

    # 3) 合同台账里的非 Word/PDF 记录：无关联节点/财务资料/变更则删除，否则仅报告
    for c in db.query(Contract).all():
        if _is_contract_ext(c.original_name):
            continue
        has_links = (
            db.query(ContractNode).filter(ContractNode.contract_id == c.id).count() > 0
            or db.query(FinancialDoc).filter(FinancialDoc.contract_id == c.id).count() > 0
            or db.query(ContractNodeChange).filter(ContractNodeChange.contract_id == c.id).count() > 0
        )
        if has_links:
            bad_contract_ledger.append({"id": c.id, "original_name": c.original_name})
        else:
            removed_contracts.append({"id": c.id, "original_name": c.original_name})
            db.delete(c)

    db.commit()
    return {
        "fixed_non_contract_files": fixed_contracts,
        "tagged_financial_files": tagged_finance,
        "removed_contracts": removed_contracts,
        "bad_contract_ledger": bad_contract_ledger,
    }


@app.get("/funding/financial-files", summary="经费管理引用：自动归集的财务单据文件（发票/结算单/暂估单/估算单等）")
def list_financial_files(
    project_id: Optional[int] = None,
    doc_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """返回文件库中已自动归集为「财务单据」的文件（含金额/日期/类型标签），供经费管理模块自动引用。"""
    pmap = {p.id: p.name for p in db.query(Project).all()}
    q = db.query(ProjectFile).filter(ProjectFile.category == "财务单据")
    if project_id is not None:
        q = q.filter(ProjectFile.project_id == project_id)
    if doc_type:
        q = q.filter(ProjectFile.doc_type == doc_type)
    items = q.order_by(ProjectFile.doc_date.desc(), ProjectFile.id.desc()).all()
    result = []
    for f in items:
        d = _file_to_dict(f)
        d["project_name"] = pmap.get(f.project_id, "")
        d["download_url"] = f"/files/{f.id}/download"
        result.append(d)
    by_type = {}
    for d in result:
        t = d["doc_type"] or "其他财务单据"
        by_type[t] = round(by_type.get(t, 0.0) + (d["amount"] or 0), 2)
    return {
        "items": result,
        "total_amount": round(sum(d["amount"] or 0 for d in result), 2),
        "by_type": by_type,
    }


@app.post("/projects/{project_id}/stats", summary="合同统计")
def project_stats(project_id: int, req: AskRequest, db: Session = Depends(get_db)):
    """从合同摘要表里读该项目所有合同的关键字段，做精确统计（数字由数据库算出，不靠大模型猜）"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    rows = db.query(ContractSummary).filter(ContractSummary.project_id == project_id).all()
    if not rows:
        return {
            "answer": "该项目下还没有已提取的合同摘要。请先上传合同文件，"
                      "或调用一次 /projects/{id}/reindex 做历史补提取。",
            "references": [],
        }


    # 数字由数据库精确算出，不再被截断/漏掉
    total_count = len(rows)
    total_amount = round(sum(r.amount_value or 0 for r in rows), 2)

    by_type = {}
    for r in rows:
        t = r.contract_type or "未分类"
        by_type[t] = by_type.get(t, 0) + 1

    items = [
        {
            "file_id": r.file_id,
            "contract_name": r.contract_name,
            "contract_no": r.contract_no,
            "party_a": r.party_a,
            "party_b": r.party_b,
            "amount_value": r.amount_value,
            "currency": r.currency,
            "service_content": r.service_content,
            "sign_date": r.sign_date,
            "term": r.term,
            "contract_type": r.contract_type,
            "service_period": r.service_period,
        }
        for r in rows
    ]

    # 把精确统计结果交给大模型，用自然语言回答用户问题
    summary_prompt = (
        "下面是某项目里所有合同的关键字段列表。请根据用户问题做统计汇总，逐项列出，不要遗漏任何一条。"
        "涉及金额、数量请给出合计和明细。\n\n"
        f"合同总数：{total_count}\n"
        f"金额合计：{total_amount}\n"
        f"按类型分布：{json.dumps(by_type, ensure_ascii=False)}\n\n"
        f"合同明细：\n{json.dumps(items, ensure_ascii=False)}\n\n"
        f"用户问题：{req.question}"
    )
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": summary_prompt}],
        "temperature": 0.1,
        "max_tokens": 8100,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    answer = resp.json()["choices"][0]["message"]["content"]

    references = [
        {"original_name": r.contract_name or "合同", "project_name": project.name}
        for r in rows
    ]
    return {
        "answer": answer,
        "total_contracts": total_count,
        "total_amount": total_amount,
        "by_type": by_type,
        "items": items,
        "references": references,
    }

@app.post("/projects/{project_id}/documents/summarize", summary="项目资料完整总结")
def documents_summarize(project_id: int, req: AskRequest, db: Session = Depends(get_db)):
    """把该项目下所有项目资料的摘要一次性喂给大模型做总结，一份都不会漏"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    rows = db.query(DocumentSummary).filter(DocumentSummary.project_id == project_id).all()
    if not rows:
        return {
            "answer": "该项目下还没有已提取的项目资料摘要。请先上传资料文件，"
                      "或调用一次 /projects/{id}/reindex 做历史补提取。",
            "references": [],
        }

    items = [
        {"doc_type": r.doc_type or "其他", "summary": r.summary or ""}
        for r in rows
    ]

    prompt = (
        "下面是某个项目下所有项目资料（方案、报告、纪要、申请表等）的清单和每份资料的一句话摘要。"
        "请根据用户问题做完整的总结/梳理，逐项列出，一份资料都不要遗漏。\n\n"
        f"资料清单：\n{json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
        f"用户问题：{req.question}"
    )
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 8100,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    answer = resp.json()["choices"][0]["message"]["content"]

    references = [
        {"original_name": r.doc_type or "资料", "project_name": project.name}
        for r in rows
    ]
    return {"answer": answer, "total": len(rows), "items": items, "references": references}

@app.get("/projects/{project_id}/documents/by-stage")
def documents_by_stage(project_id: int, db: Session = Depends(get_db)):
    """按项目阶段分组返回该项目全部资料摘要，供前端分块展示"""
    rows = (
        db.query(DocumentSummary, ProjectFile)
        .join(ProjectFile, ProjectFile.id == DocumentSummary.file_id)
        .filter(DocumentSummary.project_id == project_id)
        .all()
    )

    order = ["项目立项", "项目投决", "项目实施", "项目验收"]
    grouped = {s: [] for s in order}

    for doc, f in rows:
        stage = _normalize_stage(doc.stage, doc.doc_type)
        grouped[stage].append({
            "file_id": f.id,
            "original_name": f.original_name,
            "doc_type": doc.doc_type,
            "stage": stage,
            "summary": doc.summary,
        })

    return {
        "project_id": project_id,
        "stages": [
            {"stage": s, "documents": grouped[s]}
            for s in order
        ],
    }


# ============ 跨项目统计与报告 ============
DOC_TYPES_16 = [
    "年度科技项目申请汇总表", "科技项目申请书", "技术方案", "技术方案审查意见",
    "可研报告", "可研审查意见", "责任承诺书", "任务书", "验收申请表", "执行情况总结报告",
    "经费决算表", "经济分析报告", "成员培养成长情况总结", "验收意见", "进展报表", "延期申请表",
]
ACHIEVEMENT_TYPES = ["专利", "论文", "软件著作权", "获奖", "标准", "成果登记", "鉴定报告"]


def _extract_cross_project_target(question: str) -> dict:
    """用 LLM 从问题中提取跨项目检索目标：category + doc_type + keywords。失败返回空 dict。"""
    prompt = (
        "你是检索专家。用户想跨项目检索/统计文件，请从问题中提取检索目标，只返回一个 JSON 对象：\n"
        "{\"category\": \"\", \"doc_type\": \"\", \"keywords\": []}\n"
        "- category 从 资料、成果、合同、附件 里选一个；如果问题没有明确大类，填空串。\n"
        "- doc_type 尽量对应下面这些类型名之一（找不到就填最接近的短语或空串）："
        + "、".join(DOC_TYPES_16 + ACHIEVEMENT_TYPES) + "\n"
        "- keywords 填 1~3 个用于模糊匹配的关键词。\n"
        "例如：\"汇总所有项目的科技项目申请书\" → {\"category\":\"资料\",\"doc_type\":\"科技项目申请书\",\"keywords\":[\"申请书\"]}\n"
        "例如：\"统计所有项目的专利成果\" → {\"category\":\"成果\",\"doc_type\":\"专利\",\"keywords\":[\"专利\"]}\n"
        "只输出 JSON，禁止 markdown 代码块和任何解释。\n\n问题：" + question
    )
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 400,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    return {
        "category": str(data.get("category") or "").strip(),
        "doc_type": str(data.get("doc_type") or "").strip(),
        "keywords": [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()],
    }


def _match_cross_project(f, target: dict) -> bool:
    """判断单个文件是否命中跨项目检索目标。"""
    cat = (target.get("category") or "").strip()
    dtype = (target.get("doc_type") or "").strip()
    kws = [k for k in (target.get("keywords") or []) if k]
    if not cat and not dtype and not kws:
        return True
    if cat and f.category != cat:
        return False
    if dtype:
        if dtype in (f.doc_type or "") or dtype in (f.original_name or ""):
            return True
        return False
    blob = " ".join([f.original_name or "", f.doc_type or "", f.ai_summary or ""])
    return any(k in blob for k in kws)


def _call_deepseek_cross_project(question: str, target: dict, stats: dict, items: list) -> str:
    item_lines = []
    for m in items:
        item_lines.append(
            f"- {m['project_name']}：{m['original_name']}（{m['doc_type'] or m['category']}）摘要：{m['summary']}"
        )
    item_text = "\n".join(item_lines) if item_lines else "（无）"
    prompt = (
        "你是科技项目管理助手。下面是根据用户问题跨项目检索到的文件清单及统计结果。\n"
        f"检索目标：{json.dumps(target, ensure_ascii=False)}\n"
        f"统计：共 {stats['total']} 份，涉及 {stats['project_count']} 个项目；"
        f"按项目分布：{json.dumps(stats['per_project'], ensure_ascii=False)}\n\n"
        f"文件清单：\n{item_text}\n\n"
        f"用户问题：{question}\n\n"
        "请据此回答：先给出总体统计（各项目分别多少份、合计多少份），再逐项列出明细。"
        "如果用户要求撰写报告/总结，请按报告格式输出（含标题、分项目统计、明细、结论）。"
        "涉及具体文件时用（来源：文件名）标注。不要编造清单以外的内容。"
    )
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 8100,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


@app.post("/cross-project/analyze", summary="跨项目统计与报告")
def cross_project_analyze(req: CrossProjectRequest, db: Session = Depends(get_db)):
    """跨项目筛选文件 → 汇总统计 → 可选生成报告（LLM）。"""
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 1) 用 LLM 从问题中提取目标（category / doc_type / keywords）
    try:
        target = _extract_cross_project_target(question)
    except Exception as e:
        logger.warning(f"[跨项目] 目标提取失败，退回空：{e}")
        target = {}
    # 允许前端显式指定覆盖
    if req.category:
        target["category"] = req.category
    if req.doc_type:
        target["doc_type"] = req.doc_type

    # 2) 跨项目筛选
    rows = (
        db.query(ProjectFile, Project)
        .join(Project, ProjectFile.project_id == Project.id)
        .all()
    )
    matched = []
    for f, p in rows:
        if _match_cross_project(f, target):
            matched.append({
                "file_id": f.id,
                "original_name": f.original_name,
                "project_id": p.id,
                "project_name": p.name,
                "project_code": p.code,
                "category": f.category or "其他",
                "doc_type": f.doc_type or "",
                "stage": f.stage or "",
                "summary": f.ai_summary or "",
                "download_url": f"/files/{f.id}/download",
            })

    # 3) 汇总统计
    per_project = {}
    for m in matched:
        per_project[m["project_name"]] = per_project.get(m["project_name"], 0) + 1
    stats = {
        "total": len(matched),
        "project_count": len(per_project),
        "per_project": per_project,
    }

    if not matched:
        return {
            "answer": "没有在任意项目中找到匹配的文件，请调整描述后再试。",
            "target": target,
            "stats": stats,
            "items": [],
            "references": [],
        }

    # 4) 生成统计/报告
    try:
        answer = _call_deepseek_cross_project(question, target, stats, matched)
    except Exception as e:
        logger.error(f"[跨项目] 生成报告失败：{e}")
        answer = (f"（报告生成失败：{e}）\n\n"
                  f"统计结果：共 {stats['total']} 份，涉及 {stats['project_count']} 个项目。")

    references = [
        {
            "original_name": m["original_name"],
            "project_name": m["project_name"],
            "category": m["category"],
            "doc_type": m["doc_type"],
            "download_url": m["download_url"],
        }
        for m in matched
    ]
    return {
        "answer": answer,
        "target": target,
        "stats": stats,
        "items": matched,
        "references": references,
    }


# ============ 合同管理模块 ============
def _parse_date(s: str):
    """把各种日期字符串解析成 date，失败返回 None。"""
    if not s:
        return None
    m = re.search(r"(\d{4})\s*[年/\-\.]\s*(\d{1,2})\s*[月/\-\.]\s*(\d{1,2})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def _date_in_range(date_str, start=None, end=None) -> bool:
    """判断日期字符串是否落在 [start, end]（含边界）。
    - 无 start/end：全部命中；
    - 带筛选时：空值或解析失败不命中；
    - start/end 解析失败则忽略对应边界。"""
    d = _parse_date(date_str)
    if d is None:
        return not start and not end
    if start:
        s = _parse_date(start)
        if s and d < s:
            return False
    if end:
        e = _parse_date(end)
        if e and d > e:
            return False
    return True


def _infer_due_date(sign_date: str, term: str):
    """根据签署日期 + 期限（如 12个月/365天/3年）推断到期日，推断不出返回 None。"""
    base = _parse_date(sign_date)
    if not base or not term:
        return None
    days = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(年|个月|月|天|日|周|星期)", str(term)):
        try:
            n = float(num)
        except ValueError:
            continue
        if unit == "年":
            days += n * 365
        elif unit in ("个月", "月"):
            days += n * 30
        elif unit in ("天", "日"):
            days += n
        elif unit in ("周", "星期"):
            days += n * 7
    if days <= 0:
        return None
    return base + timedelta(days=int(round(days)))


def _contract_status(due_date_str: str):
    """根据到期日返回 (状态, 剩余天数)。状态：正常/即将到期/已超期/未识别。"""
    d = _parse_date(due_date_str)
    if not d:
        return "未识别", None
    days_left = (d - date.today()).days
    if days_left < 0:
        return "已超期", days_left
    if days_left <= 30:
        return "即将到期", days_left
    return "正常", days_left


def _extract_contract_fields(content: str, filename: str = "") -> dict:
    """用 DeepSeek 从合同文本抽取关键字段（履约时间/金额/工作内容等）。失败返回空 dict。"""
    if not content or not content.strip():
        return {}
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    text_part = content[:8000] + "\n……（中间省略）……\n" + content[-2000:]
    prompt = (
        "你是合同信息抽取专家。请阅读下面这份合同文本，抽取关键字段，只返回一个 JSON 对象：\n"
        "{\n"
        "  \"contract_name\": \"合同名称\",\n"
        "  \"contract_no\": \"合同编号\",\n"
        "  \"party_a\": \"甲方/发包方\",\n"
        "  \"party_b\": \"乙方/承包方\",\n"
        "  \"amount_value\": 0,\n"
        "  \"currency\": \"币种\",\n"
        "  \"service_content\": \"工作内容（简要概括）\",\n"
        "  \"sign_date\": \"签署日期\",\n"
        "  \"start_date\": \"履约开始时间\",\n"
        "  \"end_date\": \"履约结束时间/到期时间\",\n"
        "  \"term\": \"履约期限（如：12个月、365天、3年）\",\n"
        "  \"contract_type\": \"合同类型（技术服务/采购/施工/租赁/咨询等）\",\n"
        "  \"amount_ex_tax\": 0,\n"
        "  \"tax_rate\": 0,\n"
        "  \"tax_amount\": 0,\n"
        "  \"amount_incl_tax\": 0,\n"
        "  \"warranty_ratio\": 0,\n"
        "  \"warranty_period\": \"\",\n"
        "  \"payment_nodes\": [],\n"
        "  \"business_nodes\": []\n"
        "}\n\n"
        "字段说明：\n"
        "- amount_ex_tax 不含税金额、tax_rate 税率（小数，6% 填 0.06）、tax_amount 税额、amount_incl_tax 含税金额；没有填 0\n"
        "- warranty_ratio 质保金比例（小数，5% 填 0.05）、warranty_period 质保金到期时长（如\"12个月\"\"1年\"\"2年\"）；没有填 0 和空串\n"
        "- contract_name 必须是能区分具体标的的名称（如\"俄公堡电站监控系统国产化改造\"）；若正文标题是\"物资设备采购合同\"\"采购合同\"\"合同\"等泛化标题，务必结合文件名与正文标的（项目/设备/服务名称）提炼具体名称\n"
        "- payment_nodes 付款节点数组，每项 {\"name\":\"节点名（预付款/进度款/验收款/质保金等）\",\"ratio\":比例小数,\"amount\":金额纯数字,\"date\":\"约定付款日期YYYY-MM-DD\"}\n"
        "- business_nodes 业务节点数组，每项 {\"name\":\"节点名（提交进度报告/验收/交付等）\",\"date\":\"约定日期YYYY-MM-DD\"}\n\n"
        "严格规则：\n"
        "1. 金额必须是纯数字（去掉元/万元/货币符号/逗号/中文大写），\"壹佰万元整\"→1000000，\"100万\"→1000000\n"
        "2. 日期统一 YYYY-MM-DD 格式，没有就填空串\n"
        "3. 付款节点从\"付款方式/支付条款/结算方式/预付款/进度款/验收款/质保金\"等描述提取\n"
        "4. 业务节点从\"验收/进度报告/交付/里程碑\"等描述提取\n"
        "5. 只输出 JSON，禁止 markdown 代码块和任何解释\n\n"
        f"文件名：{filename}\n"
        f"合同文本：\n{text_part}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 3000,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}

    def _f(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    def _s(v):
        return str(v or "").strip()

    def _nodes(v):
        out = []
        for item in (v or []):
            if not isinstance(item, dict):
                continue
            out.append({
                "name": str(item.get("name") or "").strip(),
                "ratio": _f(item.get("ratio")),
                "amount": _f(item.get("amount")),
                "date": str(item.get("date") or "").strip(),
            })
        return out

    def _biz_nodes(v):
        out = []
        for item in (v or []):
            if not isinstance(item, dict):
                continue
            out.append({
                "name": str(item.get("name") or "").strip(),
                "date": str(item.get("date") or "").strip(),
            })
        return out

    return {
        "contract_name": _s(data.get("contract_name")),
        "contract_no": _s(data.get("contract_no")),
        "party_a": _s(data.get("party_a")),
        "party_b": _s(data.get("party_b")),
        "amount_value": _f(data.get("amount_value")),
        "currency": _s(data.get("currency")),
        "service_content": _s(data.get("service_content")),
        "sign_date": _s(data.get("sign_date")),
        "start_date": _s(data.get("start_date")),
        "end_date": _s(data.get("end_date")),
        "term": _s(data.get("term")),
        "contract_type": _s(data.get("contract_type")),
        "amount_ex_tax": _f(data.get("amount_ex_tax")),
        "tax_rate": _f(data.get("tax_rate")),
        "tax_amount": _f(data.get("tax_amount")),
        "amount_incl_tax": _f(data.get("amount_incl_tax")),
        "warranty_ratio": _f(data.get("warranty_ratio")),
        "warranty_period": _s(data.get("warranty_period")),
        "payment_nodes": _nodes(data.get("payment_nodes")),
        "business_nodes": _biz_nodes(data.get("business_nodes")),
    }


def _contract_to_dict(c: Contract) -> dict:
    status, days_left = _contract_status(c.due_date)
    return {
        "id": c.id,
        "original_name": c.original_name,
        "contract_name": c.contract_name or "",
        "contract_no": c.contract_no or "",
        "party_a": c.party_a or "",
        "party_b": c.party_b or "",
        "amount_value": c.amount_value or 0,
        "currency": c.currency or "",
        "service_content": c.service_content or "",
        "sign_date": c.sign_date or "",
        "start_date": c.start_date or "",
        "end_date": c.end_date or "",
        "due_date": c.due_date or "",
        "term": c.term or "",
        "contract_type": c.contract_type or "",
        "project_id": c.project_id,
        "amount_ex_tax": c.amount_ex_tax or 0,
        "tax_rate": c.tax_rate or 0,
        "tax_amount": c.tax_amount or 0,
        "amount_incl_tax": c.amount_incl_tax or 0,
        "warranty_ratio": c.warranty_ratio or 0,
        "warranty_period": c.warranty_period or "",
        "warranty_due_date": c.warranty_due_date or "",
        "confirmed": c.confirmed or 0,
        "plan_generated": c.plan_generated or 0,
        "source_file_id": c.source_file_id,
        "alert_ignored": c.alert_ignored or 0,
        "status": status,
        "days_left": days_left,
        "size": c.size,
        "processing_status": c.processing_status or "done",
        "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
        "download_url": f"/contracts/{c.id}/download",
    }


def _infer_warranty_due(base_date: str, warranty_period: str):
    """根据基准日 + 质保期（如 12个月/1年/2年）推断质保金到期日。返回 YYYY-MM-DD 或 None。"""
    base = _parse_date(base_date)
    if not base or not warranty_period:
        return None
    days = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(年|个月|月|天|日|周|星期)", str(warranty_period)):
        try:
            n = float(num)
        except ValueError:
            continue
        if unit == "年":
            days += n * 365
        elif unit in ("个月", "月"):
            days += n * 30
        elif unit in ("天", "日"):
            days += n
        elif unit in ("周", "星期"):
            days += n * 7
    if days <= 0:
        return None
    return (base + timedelta(days=int(round(days)))).strftime("%Y-%m-%d")


def _build_contract_nodes(db, contract_id: int, fields: dict):
    """根据抽取结果生成执行计划节点（付款节点 + 业务节点），初始状态「待确认」，人工确认后生效。"""
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if c is None:
        return
    project_id = c.project_id
    # 已存在的节点先清空重建（重跑/重解析时避免重复）
    db.query(ContractNode).filter(ContractNode.contract_id == contract_id).delete()
    for pn in fields.get("payment_nodes") or []:
        name = (pn.get("name") or "").strip()
        if not name:
            continue
        db.add(ContractNode(
            contract_id=contract_id, project_id=project_id,
            node_type="payment", node_name=name,
            planned_date=pn.get("date") or None,
            amount=pn.get("amount") or None,
            ratio=pn.get("ratio") or None,
            status="待确认", remark="LLM 自动抽取，待人工确认",
        ))
    for bn in fields.get("business_nodes") or []:
        name = (bn.get("name") or "").strip()
        if not name:
            continue
        db.add(ContractNode(
            contract_id=contract_id, project_id=project_id,
            node_type="business", node_name=name,
            planned_date=bn.get("date") or None,
            amount=None, ratio=None,
            status="待确认", remark="LLM 自动抽取，待人工确认",
        ))
    db.commit()


def _process_contract_async(contract_id: int, file_path: str, original_name: str):
    """后台线程：提取合同全文（含扫描件 OCR）→ LLM 抽取关键字段 → 推断到期日 → 回填数据库，最后置 done/error。"""
    db = SessionLocal()
    try:
        c = db.query(Contract).filter(Contract.id == contract_id).first()
        if c is None:
            return

        # 优先复用已预填的全文（从文件管理导入时会复用项目文件已提取文本，避免重复 OCR），否则现场提取
        content = c.content or ""
        if not content or not content.strip():
            try:
                content = extract_text_from_file(file_path)
            except Exception as e:
                logger.error(f"[合同后台] 提取文本失败 {original_name}：{e}")

        fields = {}
        if content and content.strip():
            try:
                fields = _extract_contract_fields(content, original_name)
            except Exception as e:
                logger.error(f"[合同后台] 抽取字段失败 {original_name}：{e}")

        # 到期日：优先用抽取的 end_date，否则用 sign_date+term 推断
        due_date = fields.get("end_date") or ""
        if not due_date:
            inferred = _infer_due_date(fields.get("sign_date"), fields.get("term"))
            if inferred:
                due_date = inferred.strftime("%Y-%m-%d")

        c.content = content or None
        c.contract_name = fields.get("contract_name") or None
        c.contract_no = fields.get("contract_no") or None
        c.party_a = fields.get("party_a") or None
        c.party_b = fields.get("party_b") or None
        c.amount_value = fields.get("amount_value") or 0.0
        c.currency = fields.get("currency") or None
        c.service_content = fields.get("service_content") or None
        c.sign_date = fields.get("sign_date") or None
        c.start_date = fields.get("start_date") or None
        c.end_date = fields.get("end_date") or None
        c.due_date = due_date or None
        c.term = fields.get("term") or None
        c.contract_type = fields.get("contract_type") or None
        # 新增字段：金额拆分 + 质保金
        c.amount_ex_tax = fields.get("amount_ex_tax")
        c.tax_rate = fields.get("tax_rate")
        c.tax_amount = fields.get("tax_amount")
        c.amount_incl_tax = fields.get("amount_incl_tax")
        c.warranty_ratio = fields.get("warranty_ratio")
        c.warranty_period = fields.get("warranty_period") or None
        c.warranty_due_date = _infer_warranty_due(due_date, fields.get("warranty_period"))
        c.processing_status = "done"
        db.commit()

        # 生成全周期执行计划节点（付款节点 + 业务节点，待人工确认）
        _build_contract_nodes(db, contract_id, fields)
        logger.info(f"[合同后台] 解析完成 {original_name}")
    except Exception as e:
        logger.error(f"[合同后台] 处理异常 {original_name}：{e}")
        try:
            c = db.query(Contract).filter(Contract.id == contract_id).first()
            if c is not None:
                c.processing_status = "error"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _import_contract_from_project_file(db, file_id: int):
    """把项目文件库中的一份「合同」类文件导入到独立合同台账（幂等，按 source_file_id 去重）。
    返回 (action, contract_id)：action ∈ {"imported","exists","skipped","error"}。
    导入时会拷贝文件到 data/，保证合同台账独立于项目文件库（删除项目文件不影响台账）。"""
    pf = db.query(ProjectFile).filter(ProjectFile.id == file_id).first()
    if pf is None:
        return ("skipped", None)
    # 合同格式硬约束：非 Word/PDF 不导入合同台账
    if not _is_contract_ext(pf.original_name):
        return ("skipped", None)
    # 幂等：该文件已导入过
    existing = db.query(Contract).filter(Contract.source_file_id == file_id).first()
    if existing is not None:
        return ("exists", existing.id)

    src_path = pf.file_path
    if not src_path or not os.path.exists(src_path):
        return ("skipped", None)
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    ext = os.path.splitext(pf.original_name)[1]
    stored = f"contract_import_{ts}{ext}"
    dst = os.path.join(UPLOAD_DIR, stored)
    try:
        shutil.copyfile(src_path, dst)
    except Exception as e:
        logger.error(f"[导入合同] 拷贝文件失败 {pf.original_name}：{e}")
        return ("error", None)

    # 复用项目文件已提取的全文（优先磁盘，其次 content 字段），避免扫描件合同重复 OCR
    imported_content = ""
    try:
        imported_content = _load_content(pf) or ""
    except Exception:
        imported_content = pf.content or ""

    c = Contract(
        original_name=pf.original_name,
        stored_name=stored,
        file_path=dst,
        size=os.path.getsize(dst),
        project_id=pf.project_id,
        source_file_id=file_id,
        content=imported_content or None,
        processing_status="processing",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    _ocr_executor.submit(_process_contract_async, c.id, dst, pf.original_name)
    logger.info(f"[导入合同] 项目文件 #{file_id}「{pf.original_name}」→ 合同台账 #{c.id}")
    return ("imported", c.id)


@app.get("/contracts/import-candidates", summary="从文件管理扫描可导入合同的候选文件")
def contract_import_candidates(db: Session = Depends(get_db)):
    """扫描项目文件库中 category=合同 且为 Word/PDF 的文件，标注是否已导入合同台账。"""
    pmap = {p.id: p.name for p in db.query(Project).all()}
    imported_ids = {c.source_file_id for c in db.query(Contract).filter(Contract.source_file_id.isnot(None)).all()}
    files = (
        db.query(ProjectFile)
        .filter(ProjectFile.category == "合同")
        .order_by(ProjectFile.project_id.asc(), ProjectFile.id.asc())
        .all()
    )
    files = [f for f in files if _is_contract_ext(f.original_name)]
    candidates = []
    for f in files:
        candidates.append({
            "file_id": f.id,
            "original_name": f.original_name,
            "project_id": f.project_id,
            "project_name": pmap.get(f.project_id, f"项目#{f.project_id}"),
            "category": f.category or "",
            "doc_type": f.doc_type or "",
            "imported": f.id in imported_ids,
            "size": f.size or 0,
        })
    return {
        "candidates": candidates,
        "total": len(candidates),
        "imported": sum(1 for c in candidates if c["imported"]),
        "pending": sum(1 for c in candidates if not c["imported"]),
    }


class ContractImportRequest(BaseModel):
    file_ids: List[int]


@app.post("/contracts/import", summary="从文件管理批量导入合同到台账")
def contract_import(req: ContractImportRequest, db: Session = Depends(get_db)):
    imported, exists, skipped, failed = [], [], [], []
    for fid in req.file_ids:
        try:
            action, cid = _import_contract_from_project_file(db, fid)
        except Exception as e:
            logger.error(f"[导入合同] 处理 file #{fid} 失败：{e}")
            failed.append(fid)
            continue
        if action == "imported":
            imported.append(cid)
        elif action == "exists":
            exists.append(cid)
        else:
            skipped.append(fid)
    _audit("admin", "POST /contracts/import", f"从文件管理导入合同：成功 {len(imported)}，已存在 {len(exists)}，跳过 {len(skipped)}")
    return {
        "imported": imported, "exists": exists, "skipped": skipped, "failed": failed,
        "imported_count": len(imported), "exists_count": len(exists),
    }


@app.post("/contracts", summary="上传合同文件（后台异步抽取履约时间/金额/工作内容/付款节点等）")
async def upload_contracts(
    files: List[UploadFile] = File(...),
    project_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """上传立即入库返回，OCR + LLM 字段抽取放到后台线程，避免扫描件合同上传超时。"""
    results = []
    errors = []
    for file in files:
        try:
            # 合同格式硬约束：只接受 Word(.doc/.docx) 或 PDF，其他格式一律不是合同
            if not _is_contract_ext(file.filename):
                errors.append({"filename": file.filename, "error": "仅支持 Word(.doc/.docx) 或 PDF 格式的合同文件"})
                continue
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            ext = os.path.splitext(file.filename)[1]
            stored_name = f"contract_{timestamp}{ext}"
            file_path = os.path.join(UPLOAD_DIR, stored_name)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            size = os.path.getsize(file_path)

            # 只入库（状态=解析中），提取/抽取交给后台
            c = Contract(
                original_name=file.filename,
                stored_name=stored_name,
                file_path=file_path,
                size=size,
                project_id=project_id,
                processing_status="processing",
            )
            db.add(c)
            db.commit()
            db.refresh(c)

            _ocr_executor.submit(_process_contract_async, c.id, file_path, file.filename)
            results.append(_contract_to_dict(c))
        except Exception as e:
            db.rollback()
            logger.error(f"[合同] 上传失败 {file.filename}：{e}")
            errors.append({"filename": file.filename, "error": str(e)})

    return {"uploaded": results, "errors": errors}


@app.get("/contracts", summary="合同列表（可按项目筛选）")
def list_contracts(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Contract)
    if project_id is not None:
        q = q.filter(Contract.project_id == project_id)
    contracts = q.order_by(Contract.created_at.desc()).all()
    # 附带项目名，供前端下拉识别「该合同属于哪个项目」（LLM 抽取的合同名常过于泛化，需项目名兜底）
    pmap = {p.id: p.name for p in db.query(Project).all()}
    result = []
    for c in contracts:
        d = _contract_to_dict(c)
        d["project_name"] = pmap.get(c.project_id, "")
        result.append(d)
    return result


# ============ 合同：人工确认 + 字段编辑 + 执行计划节点 + 变更追溯 ============

class ContractUpdateRequest(BaseModel):
    contract_name: Optional[str] = None
    contract_no: Optional[str] = None
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    amount_value: Optional[float] = None
    amount_ex_tax: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    amount_incl_tax: Optional[float] = None
    warranty_ratio: Optional[float] = None
    warranty_period: Optional[str] = None
    warranty_due_date: Optional[str] = None
    sign_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    due_date: Optional[str] = None
    term: Optional[str] = None
    contract_type: Optional[str] = None
    service_content: Optional[str] = None
    project_id: Optional[int] = None


@app.put("/contracts/{contract_id}", summary="编辑合同字段（人工校对修正）")
def update_contract(contract_id: int, req: ContractUpdateRequest, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    for k, v in req.dict(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    return _contract_to_dict(c)


@app.post("/contracts/{contract_id}/confirm", summary="合同人工确认生效（抽取结果经人工核对后确认）")
def confirm_contract(contract_id: int, req: Optional[ContractUpdateRequest] = None, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    if req and req.project_id is not None:
        c.project_id = req.project_id
    c.confirmed = 1
    c.confirmed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 待确认节点 → 待办（生效）
    for n in db.query(ContractNode).filter(ContractNode.contract_id == contract_id, ContractNode.status == "待确认").all():
        n.status = "待办"
        n.remark = "人工确认生效"
        n.project_id = c.project_id
    db.commit()
    return {"message": "已确认生效", "contract": _contract_to_dict(c)}


@app.post("/contracts/{contract_id}/ignore-alert", summary="忽略该合同的风险预警（超期/临期不再提示）")
def ignore_contract_alert(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    c.alert_ignored = 1
    # 同步清除该合同的「合同超期/合同临期」待办，避免待办角标仍计数
    db.query(Todo).filter(
        Todo.category.in_(["合同超期", "合同临期"]), Todo.source_id == contract_id
    ).delete(synchronize_session=False)
    db.commit()
    _audit("admin", f"POST /contracts/{contract_id}/ignore-alert", f"忽略合同预警：{c.contract_name or c.original_name}")
    return {"message": "已忽略，该合同的风险预警不再显示", "alert_ignored": 1}


@app.post("/contracts/{contract_id}/unignore-alert", summary="取消忽略（恢复风险预警提示）")
def unignore_contract_alert(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    c.alert_ignored = 0
    db.commit()
    return {"message": "已恢复该合同的风险预警提示", "alert_ignored": 0}


def _node_to_dict(n: ContractNode) -> dict:
    return {
        "id": n.id, "contract_id": n.contract_id, "project_id": n.project_id,
        "node_type": n.node_type, "node_name": n.node_name,
        "planned_date": n.planned_date or "", "amount": n.amount or 0,
        "ratio": n.ratio or 0, "status": n.status or "待办", "remark": n.remark or "",
    }


@app.get("/contracts/{contract_id}/nodes", summary="合同执行计划节点列表（含变更记录）")
def contract_nodes(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    nodes = db.query(ContractNode).filter(ContractNode.contract_id == contract_id).order_by(ContractNode.id.asc()).all()
    changes = db.query(ContractNodeChange).filter(ContractNodeChange.contract_id == contract_id).order_by(ContractNodeChange.changed_at.desc()).all()
    return {
        "contract": _contract_to_dict(c),
        "nodes": [_node_to_dict(n) for n in nodes],
        "changes": [
            {"id": ch.id, "node_id": ch.node_id, "field": ch.field,
             "old_value": ch.old_value, "new_value": ch.new_value,
             "change_reason": ch.change_reason,
             "changed_at": ch.changed_at.strftime("%Y-%m-%d %H:%M:%S") if ch.changed_at else ""}
            for ch in changes
        ],
    }


class NodeUpdateRequest(BaseModel):
    node_name: Optional[str] = None
    planned_date: Optional[str] = None
    amount: Optional[float] = None
    ratio: Optional[float] = None
    status: Optional[str] = None
    change_reason: Optional[str] = None


@app.post("/contract-nodes/{node_id}/update", summary="更新执行计划节点（记录变更时间与原因，全程可追溯）")
def update_contract_node(node_id: int, req: NodeUpdateRequest, db: Session = Depends(get_db)):
    n = db.query(ContractNode).filter(ContractNode.id == node_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="节点不存在")
    reason = (req.change_reason or "").strip() or "手动修改"
    field_map = [
        ("node_name", req.node_name, "节点名称"),
        ("planned_date", req.planned_date, "计划日期"),
        ("amount", req.amount, "金额"),
        ("ratio", req.ratio, "比例"),
        ("status", req.status, "状态"),
    ]
    for attr, new_val, label in field_map:
        if new_val is None:
            continue
        old_val = getattr(n, attr)
        if str(old_val) != str(new_val):
            db.add(ContractNodeChange(
                node_id=n.id, contract_id=n.contract_id,
                field=label, old_value=str(old_val or ""), new_value=str(new_val or ""),
                change_reason=reason,
            ))
            setattr(n, attr, new_val)
    db.commit()
    return {"message": "已更新", "node": _node_to_dict(n)}


@app.post("/contract-nodes/{node_id}/status", summary="更新节点状态（待办/已完成/已取消）")
def update_contract_node_status(node_id: int, req: NodeUpdateRequest, db: Session = Depends(get_db)):
    n = db.query(ContractNode).filter(ContractNode.id == node_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="节点不存在")
    if req.status:
        db.add(ContractNodeChange(
            node_id=n.id, contract_id=n.contract_id,
            field="状态", old_value=str(n.status or ""), new_value=str(req.status),
            change_reason=(req.change_reason or "").strip() or "手动更新状态",
        ))
        n.status = req.status
    db.commit()
    return {"message": "已更新", "node": _node_to_dict(n)}


@app.get("/contracts/stats", summary="合同宏观统计（图表数据 + 超期/临期提醒，支持时间/项目筛选）")
def contract_stats(
    start: Optional[str] = None,
    end: Optional[str] = None,
    date_field: Optional[str] = "sign_date",
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Contract)
    if project_id is not None:
        q = q.filter(Contract.project_id == project_id)
    contracts = q.all()
    items = [_contract_to_dict(c) for c in contracts]

    # 时间筛选：按签订日期（sign_date）或到期日（due_date）过滤
    field = date_field if date_field in ("sign_date", "due_date") else "sign_date"
    if start or end:
        items = [c for c in items if _date_in_range(c.get(field), start, end)]

    total_count = len(items)
    total_amount = round(sum(c["amount_value"] for c in items), 2)

    by_type = {}
    amount_by_type = {}
    by_status = {}
    by_party_a = {}
    for c in items:
        t = c["contract_type"] or "未分类"
        by_type[t] = by_type.get(t, 0) + 1
        amount_by_type[t] = round(amount_by_type.get(t, 0) + c["amount_value"], 2)
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        a = c["party_a"] or "未识别甲方"
        by_party_a[a] = round(by_party_a.get(a, 0) + c["amount_value"], 2)

    overdue = [c for c in items if c["status"] == "已超期" and not c.get("alert_ignored")]
    upcoming = [c for c in items if c["status"] == "即将到期" and not c.get("alert_ignored")]

    return {
        "total_count": total_count,
        "total_amount": total_amount,
        "by_type": by_type,
        "amount_by_type": amount_by_type,
        "by_status": by_status,
        "by_party_a": by_party_a,
        "overdue": overdue,
        "upcoming": upcoming,
        "items": items,
    }


@app.get("/contracts/{contract_id}/download", summary="下载合同文件")
def download_contract(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    if not os.path.exists(c.file_path):
        raise HTTPException(status_code=404, detail="文件在磁盘上不存在")
    return FileResponse(path=c.file_path, filename=c.original_name, media_type="application/octet-stream")


@app.get("/contracts/{contract_id}/content", summary="查看合同提取的文本")
def contract_content(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    return {"id": c.id, "original_name": c.original_name, "content": c.content or ""}


@app.delete("/contracts/{contract_id}", summary="删除合同")
def delete_contract(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    if os.path.exists(c.file_path):
        try:
            os.remove(c.file_path)
        except Exception:
            pass
    db.delete(c)
    db.commit()
    return {"message": f"合同「{c.original_name}」已删除"}


class ContractBatchDeleteRequest(BaseModel):
    ids: List[int]


@app.post("/contracts/batch-delete", summary="批量删除合同（如一键清除超期合同）")
def batch_delete_contracts(req: ContractBatchDeleteRequest, db: Session = Depends(get_db)):
    deleted = []
    not_found = []
    for cid in req.ids:
        c = db.query(Contract).filter(Contract.id == cid).first()
        if not c:
            not_found.append(cid)
            continue
        if os.path.exists(c.file_path):
            try:
                os.remove(c.file_path)
            except Exception:
                pass
        deleted.append(c.original_name)
        db.delete(c)
    db.commit()
    return {"deleted": deleted, "not_found": not_found, "count": len(deleted)}


# ============ 合同查重去重（名称 + 内容双重判断）============
CONTRACT_DUP_SIMILARITY = 0.75  # 内容相似度阈值（归一化文本的 SequenceMatcher ratio）


def _normalize_contract_name(filename: str) -> str:
    """合同名称归一化：去扩展名、去空白、去 (OCR/扫描/文本/副本) 等冗余标记、统一小写。"""
    if not filename:
        return ""
    s = filename.strip()
    s = os.path.splitext(s)[0]
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[（(][^（）()]*?(?:OCR|扫描|文本|副本|复制)[^（）()]*?[）)]", "", s, flags=re.IGNORECASE)
    return s.lower()


def _norm_contract_text(content: str) -> str:
    """合同内容归一化：去空白、去标点/符号、统一小写，用于相似度比较。"""
    if not content:
        return ""
    s = content.lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\W_]+", "", s, flags=re.UNICODE)
    return s


def _contracts_content_similar(a: str, b: str) -> bool:
    """双重判断第二层：两份合同内容是否相似（空内容视为不相似）。"""
    na, nb = _norm_contract_text(a), _norm_contract_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # 短文本（字段抽取失败/极短）用包含关系兜底；长文本用相似度
    if min(len(na), len(nb)) < 80:
        return na in nb or nb in na
    return difflib.SequenceMatcher(None, na, nb).ratio() >= CONTRACT_DUP_SIMILARITY


def _contract_dup_groups(db: Session):
    """第一层按名称归一化分组，第二层组内按内容相似度聚类，返回重复组列表（每组仅含 >=2 条）。"""
    items = db.query(Contract).order_by(Contract.created_at.asc(), Contract.id.asc()).all()
    name_groups: dict = {}
    for c in items:
        key = _normalize_contract_name(c.original_name)
        if not key:
            continue
        name_groups.setdefault(key, []).append(c)

    dup_groups = []
    for key, lst in name_groups.items():
        clusters = []
        for c in lst:
            placed = False
            for cl in clusters:
                if _contracts_content_similar(c.content, cl[0].content):
                    cl.append(c)
                    placed = True
                    break
            if not placed:
                clusters.append([c])
        for cl in clusters:
            if len(cl) >= 2:
                cl_sorted = sorted(cl, key=lambda x: (x.created_at or datetime.min, x.id))
                dup_groups.append({
                    "key": key,
                    "count": len(cl),
                    "keep": _contract_to_dict(cl_sorted[-1]),
                    "remove": [_contract_to_dict(x) for x in cl_sorted[:-1]],
                    "remove_ids": [x.id for x in cl_sorted[:-1]],
                })
    return dup_groups


@app.get("/contracts/duplicates", summary="合同查重（只读）：按名称+内容双重判断")
def contract_duplicates(db: Session = Depends(get_db)):
    groups = _contract_dup_groups(db)
    return {
        "groups": groups,
        "group_count": len(groups),
        "total_remove": sum(len(g["remove"]) for g in groups),
    }


@app.post("/contracts/deduplicate", summary="合同去重：每组只保留最新一条，其余删除")
def contract_deduplicate(db: Session = Depends(get_db)):
    groups = _contract_dup_groups(db)
    deleted_ids, deleted_names = [], []
    for g in groups:
        for x_id in g["remove_ids"]:
            c = db.query(Contract).filter(Contract.id == x_id).first()
            if not c:
                continue
            if os.path.exists(c.file_path):
                try:
                    os.remove(c.file_path)
                except Exception:
                    pass
            deleted_ids.append(c.id)
            deleted_names.append(c.contract_name or c.original_name)
            db.delete(c)
    db.commit()
    _audit("admin", "POST /contracts/deduplicate", f"合同查重去重，删除 {len(deleted_ids)} 条重复记录")
    return {"deleted_ids": deleted_ids, "deleted_names": deleted_names, "count": len(deleted_ids)}


# ============ 经费管理 ============
FUNDING_TYPES = ["预算", "到账", "支出"]


class FundingCreate(BaseModel):
    project_id: Optional[int] = None
    item_name: str
    fund_type: Optional[str] = "到账"
    amount: float
    fund_date: Optional[str] = None
    remark: Optional[str] = None


def _funding_to_dict(f: Funding, project_name: str = "") -> dict:
    return {
        "id": f.id,
        "project_id": f.project_id,
        "project_name": project_name,
        "item_name": f.item_name,
        "fund_type": f.fund_type or "到账",
        "amount": f.amount or 0,
        "fund_date": f.fund_date or "",
        "remark": f.remark or "",
        "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S") if f.created_at else "",
    }


@app.post("/fundings", summary="新增经费记录")
def create_funding(req: FundingCreate, db: Session = Depends(get_db)):
    if not req.item_name or not req.item_name.strip():
        raise HTTPException(status_code=400, detail="经费科目必填")
    if req.amount is None:
        raise HTTPException(status_code=400, detail="金额必填")
    f = Funding(
        project_id=req.project_id,
        item_name=req.item_name.strip(),
        fund_type=req.fund_type or "到账",
        amount=req.amount,
        fund_date=req.fund_date or None,
        remark=req.remark or None,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    _audit("admin", "POST /fundings", f"新增经费：{f.item_name} {f.amount}")
    return _funding_to_dict(f)


@app.get("/fundings", summary="经费列表（可按项目筛选）")
def list_fundings(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Funding)
    if project_id is not None:
        q = q.filter(Funding.project_id == project_id)
    items = q.order_by(Funding.created_at.desc()).all()
    pmap = {p.id: p.name for p in db.query(Project).all()}
    return [_funding_to_dict(f, pmap.get(f.project_id, "")) for f in items]


@app.delete("/fundings/{funding_id}", summary="删除经费记录")
def delete_funding(funding_id: int, db: Session = Depends(get_db)):
    f = db.query(Funding).filter(Funding.id == funding_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="经费记录不存在")
    db.delete(f)
    db.commit()
    return {"message": f"经费「{f.item_name}」已删除"}


@app.get("/fundings/stats", summary="经费统计（预算/到账/支出/结余 + 分布，支持时间筛选）")
def funding_stats(start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    items = db.query(Funding).all()
    if start or end:
        items = [f for f in items if _date_in_range(f.fund_date, start, end)]
    pmap = {p.id: p.name for p in db.query(Project).all()}
    budget = sum(f.amount or 0 for f in items if f.fund_type == "预算")
    income = sum(f.amount or 0 for f in items if f.fund_type == "到账")
    expense = sum(f.amount or 0 for f in items if f.fund_type == "支出")
    by_type = {}
    by_project = {}
    for f in items:
        t = f.fund_type or "其他"
        by_type[t] = round(by_type.get(t, 0) + (f.amount or 0), 2)
        pname = pmap.get(f.project_id, "平台级")
        by_project[pname] = round(by_project.get(pname, 0) + (f.amount or 0), 2)
    return {
        "total_count": len(items),
        "budget": round(budget, 2),
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(income - expense, 2),
        "by_type": by_type,
        "by_project": by_project,
    }


# ============ 年度经费一览表（项目 × 月份 矩阵）============
class FundingGridRowCreate(BaseModel):
    year: int
    project_id: int


class FundingCellSave(BaseModel):
    year: int
    project_id: int
    month: int
    amount: Optional[float] = None
    item_name: Optional[str] = None
    fund_date: Optional[str] = None


class FundingRowMove(BaseModel):
    direction: str  # "up" / "down"


def _grid_att_to_dict(a: FundingAttachment) -> dict:
    return {
        "id": a.id,
        "year": a.year,
        "project_id": a.project_id,
        "month": a.month,
        "file_name": a.file_name,
        "download_url": f"/funding-grid/attachments/{a.id}/download",
    }


@app.get("/funding-grid", summary="年度经费一览表数据（行 + 单元格 + 附件 + 合同金额）")
def get_funding_grid(year: int, db: Session = Depends(get_db)):
    rows = (
        db.query(FundingGridRow)
        .filter(FundingGridRow.year == year)
        .order_by(FundingGridRow.sort_order.asc(), FundingGridRow.id.asc())
        .all()
    )
    pmap = {p.id: p.name for p in db.query(Project).all()}
    cells = db.query(FundingCell).filter(FundingCell.year == year).all()
    atts = db.query(FundingAttachment).filter(FundingAttachment.year == year).all()

    cell_map = {}
    for c in cells:
        cell_map[f"{c.project_id}-{c.month}"] = {
            "amount": c.amount,
            "item_name": c.item_name or "",
            "fund_date": c.fund_date or "",
        }
    att_map = {}
    for a in atts:
        att_map.setdefault(f"{a.project_id}-{a.month}", []).append(_grid_att_to_dict(a))

    # ---- 合同金额（暂估/结算/到账，按 project_id × year，override 优先）----
    override_rows = (
        db.query(ProjectContractAmountOverride)
        .filter(ProjectContractAmountOverride.year == year)
        .all()
    )
    overrides = {(o.project_id, o.kind): o for o in override_rows}
    confirmed_docs = db.query(FinancialDoc).filter(FinancialDoc.confirmed == 1).all()
    income_fundings = db.query(Funding).filter(Funding.fund_type == "到账").all()
    all_contracts = db.query(Contract).all()

    def _year_of(doc_date_str):
        dt = _parse_date(doc_date_str)
        return dt.year if dt else None

    def _auto_amount(pid, kind):
        total = 0.0
        if kind == "estimate":
            for d in confirmed_docs:
                if d.project_id == pid and d.doc_type == "暂估单" and _year_of(d.doc_date) == year:
                    total += d.amount or 0
        elif kind == "settlement":
            for d in confirmed_docs:
                if d.project_id == pid and d.doc_type == "结算单" and _year_of(d.doc_date) == year:
                    total += d.amount or 0
        else:  # income
            for f in income_fundings:
                if f.project_id == pid and _year_of(f.fund_date) == year:
                    total += f.amount or 0
        return total

    contract_amounts: dict = {}
    for r in rows:
        pid = r.project_id
        entry: dict = {}
        for kind in AMOUNT_KINDS:
            auto = round(_auto_amount(pid, kind), 2)
            ov = overrides.get((pid, kind))
            if ov is not None:
                entry[kind] = round(ov.amount or 0, 2)
                entry[f"{kind}_override"] = True
            else:
                entry[kind] = auto
                entry[f"{kind}_override"] = False
        # 合同清单：该项目全部合同，每条附本年暂估/结算金额
        clist = []
        for c in all_contracts:
            if c.project_id != pid:
                continue
            est = round(sum(
                d.amount or 0 for d in confirmed_docs
                if d.contract_id == c.id and d.doc_type == "暂估单" and _year_of(d.doc_date) == year
            ), 2)
            stl = round(sum(
                d.amount or 0 for d in confirmed_docs
                if d.contract_id == c.id and d.doc_type == "结算单" and _year_of(d.doc_date) == year
            ), 2)
            clist.append({
                "id": c.id,
                "contract_name": c.contract_name or "",
                "contract_no": c.contract_no or "",
                "amount_incl_tax": round(c.amount_incl_tax or c.amount_value or 0, 2),
                "sign_date": c.sign_date or "",
                "estimate": est,
                "settlement": stl,
            })
        entry["contracts"] = clist
        contract_amounts[str(pid)] = entry

    return {
        "year": year,
        "rows": [
            {"id": r.id, "project_id": r.project_id, "project_name": pmap.get(r.project_id, f"项目#{r.project_id}")}
            for r in rows
        ],
        "cells": cell_map,
        "attachments": att_map,
        "contract_amounts": contract_amounts,
    }


class ContractAmountOverrideRequest(BaseModel):
    project_id: int
    year: int
    kind: str
    amount: Optional[float] = None   # None 表示删除记录，恢复自动值


@app.post("/funding-grid/contract-amount-override", summary="人工修改/重置合同金额（暂估/结算/到账，override 优先于自动值）")
def contract_amount_override(req: ContractAmountOverrideRequest, db: Session = Depends(get_db)):
    if req.kind not in AMOUNT_KINDS:
        raise HTTPException(status_code=400, detail=f"kind 须为 {AMOUNT_KINDS} 之一")
    row = (
        db.query(ProjectContractAmountOverride)
        .filter(
            ProjectContractAmountOverride.project_id == req.project_id,
            ProjectContractAmountOverride.year == req.year,
            ProjectContractAmountOverride.kind == req.kind,
        )
        .first()
    )
    if req.amount is None:
        if row:
            db.delete(row)
            db.commit()
        return {"message": "已恢复自动值"}
    if row:
        row.amount = req.amount
        row.updated_at = datetime.utcnow()
    else:
        db.add(ProjectContractAmountOverride(
            project_id=req.project_id, year=req.year, kind=req.kind, amount=req.amount
        ))
    db.commit()
    return {"message": "已保存"}


@app.get("/funding-grid/summary", summary="项目全周期经费总览（跨年自动汇总）")
def get_funding_grid_summary(db: Session = Depends(get_db)):
    """按项目聚合所有年度的经费金额，返回每个项目的年度分布、总计与跨年标记。
    用于跨年项目的「全生命周期」经费视角，纯聚合查询，不影响年度矩阵。"""
    pmap = {p.id: p.name for p in db.query(Project).all()}
    rows = db.query(FundingGridRow).all()
    cells = db.query(FundingCell).all()

    # project_id -> 出现过的年度集合（纳入一览表的行 + 有经费的单元格都算）
    proj_years: dict = {}
    for r in rows:
        proj_years.setdefault(r.project_id, set()).add(r.year)
    for c in cells:
        proj_years.setdefault(c.project_id, set()).add(c.year)

    # (project_id, year) -> 金额合计
    yt_raw: dict = {}
    for c in cells:
        if c.amount:
            key = (c.project_id, c.year)
            yt_raw[key] = yt_raw.get(key, 0.0) + float(c.amount)

    projects = []
    for pid, ys in proj_years.items():
        ys_sorted = sorted(ys)
        yt = {str(y): round(yt_raw.get((pid, y), 0.0), 2) for y in ys_sorted}
        total = round(sum(yt.values()), 2)
        projects.append({
            "project_id": pid,
            "project_name": pmap.get(pid, f"项目#{pid}"),
            "years": ys_sorted,
            "year_totals": yt,
            "total": total,
            "year_count": len(ys_sorted),
            "is_cross_year": len(ys_sorted) > 1,
        })
    projects.sort(key=lambda x: -x["total"])
    cross = sum(1 for x in projects if x["is_cross_year"])
    all_years = sorted({y for ys in proj_years.values() for y in ys})
    return {
        "projects": projects,
        "total_count": len(projects),
        "cross_year_count": cross,
        "years_all": all_years,
    }


@app.post("/funding-grid/rows", summary="新增年度经费表项目行")
def add_funding_grid_row(req: FundingGridRowCreate, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    exists = (
        db.query(FundingGridRow)
        .filter(FundingGridRow.year == req.year, FundingGridRow.project_id == req.project_id)
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="该项目已添加，请勿重复添加")
    last = (
        db.query(FundingGridRow)
        .filter(FundingGridRow.year == req.year)
        .order_by(FundingGridRow.sort_order.desc())
        .first()
    )
    new_order = (last.sort_order + 1) if last else 1
    r = FundingGridRow(year=req.year, project_id=req.project_id, sort_order=new_order)
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "project_id": r.project_id, "project_name": project.name}


@app.delete("/funding-grid/rows/{row_id}", summary="删除年度经费表项目行（连带删除其单元格与附件）")
def delete_funding_grid_row(row_id: int, db: Session = Depends(get_db)):
    r = db.query(FundingGridRow).filter(FundingGridRow.id == row_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="行不存在")
    year, pid = r.year, r.project_id
    for a in db.query(FundingAttachment).filter(FundingAttachment.year == year, FundingAttachment.project_id == pid).all():
        if os.path.exists(a.file_path):
            try:
                os.remove(a.file_path)
            except Exception:
                pass
        db.delete(a)
    db.query(FundingCell).filter(FundingCell.year == year, FundingCell.project_id == pid).delete()
    db.delete(r)
    db.commit()
    return {"message": "已删除该行及其经费数据"}


@app.post("/funding-grid/rows/{row_id}/move", summary="调整项目行顺序（上移/下移）")
def move_funding_grid_row(row_id: int, req: FundingRowMove, db: Session = Depends(get_db)):
    r = db.query(FundingGridRow).filter(FundingGridRow.id == row_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="行不存在")
    siblings = (
        db.query(FundingGridRow)
        .filter(FundingGridRow.year == r.year)
        .order_by(FundingGridRow.sort_order.asc(), FundingGridRow.id.asc())
        .all()
    )
    idx = next((i for i, x in enumerate(siblings) if x.id == r.id), None)
    if idx is None:
        return {"message": "ok"}
    swap_idx = idx - 1 if req.direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(siblings):
        return {"message": "ok"}
    other = siblings[swap_idx]
    r.sort_order, other.sort_order = other.sort_order, r.sort_order
    db.commit()
    return {"message": "ok"}


@app.post("/funding-grid/cells", summary="保存单元格（项目×月份 的金额/科目/日期）")
def save_funding_cell(req: FundingCellSave, db: Session = Depends(get_db)):
    if req.month < 1 or req.month > 12:
        raise HTTPException(status_code=400, detail="月份需在 1~12 之间")
    cell = (
        db.query(FundingCell)
        .filter(FundingCell.year == req.year, FundingCell.project_id == req.project_id, FundingCell.month == req.month)
        .first()
    )
    amount = req.amount
    item_name = (req.item_name or "").strip() or None
    fund_date = (req.fund_date or "").strip() or None
    if cell is None:
        cell = FundingCell(year=req.year, project_id=req.project_id, month=req.month,
                           amount=amount, item_name=item_name, fund_date=fund_date)
        db.add(cell)
    else:
        cell.amount = amount
        cell.item_name = item_name
        cell.fund_date = fund_date
    db.commit()
    return {"message": "已保存"}


@app.post("/funding-grid/attachments", summary="上传单元格附件（项目×月份，可多文件）")
async def upload_funding_attachments(
    year: int = Form(...),
    project_id: int = Form(...),
    month: int = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    results, errors = [], []
    for f in files:
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
            ext = os.path.splitext(f.filename)[1]
            stored = f"funding_{ts}{ext}"
            path = os.path.join(UPLOAD_DIR, stored)
            with open(path, "wb") as buf:
                shutil.copyfileobj(f.file, buf)
            a = FundingAttachment(year=year, project_id=project_id, month=month,
                                  file_name=f.filename, file_path=path)
            db.add(a)
            db.commit()
            db.refresh(a)
            results.append(_grid_att_to_dict(a))
        except Exception as e:
            db.rollback()
            errors.append({"filename": f.filename, "error": str(e)})
    return {"uploaded": results, "errors": errors}


@app.get("/funding-grid/attachments/{att_id}/download", summary="下载单元格附件")
def download_funding_attachment(att_id: int, db: Session = Depends(get_db)):
    a = db.query(FundingAttachment).filter(FundingAttachment.id == att_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="附件不存在")
    if not os.path.exists(a.file_path):
        raise HTTPException(status_code=404, detail="文件在磁盘上不存在")
    return FileResponse(path=a.file_path, filename=a.file_name, media_type="application/octet-stream")


@app.delete("/funding-grid/attachments/{att_id}", summary="删除单元格附件")
def delete_funding_attachment(att_id: int, db: Session = Depends(get_db)):
    a = db.query(FundingAttachment).filter(FundingAttachment.id == att_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="附件不存在")
    if os.path.exists(a.file_path):
        try:
            os.remove(a.file_path)
        except Exception:
            pass
    db.delete(a)
    db.commit()
    return {"message": "附件已删除"}


# ============ 财务资料（暂估单 / 结算单 / 发票） ============
FINANCIAL_DOC_TYPES = ["暂估单", "结算单", "发票"]

# —— 财务单据金额/税额/日期的正则兜底提取（OCR 文本噪声大、LLM 漏识别时兜底）——
# 注意：OCR 常把「¥/￥」识别成「Y」
_MONEY_RE = re.compile(r"[¥￥Y]\s*([0-9][0-9,]*\.?\d{0,2})")
_DATE_RE = re.compile(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})日?")


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except Exception:
        return 0.0


def _regex_find_amount(text):
    """按「价税合计/合计金额」关键词定位价税合计金额；找不到再取全文最大 ¥/￥/Y 金额。"""
    for kw in ("价税合计", "合计金额", "金额合计", "价税合计金额"):
        idx = text.find(kw)
        if idx == -1:
            continue
        seg = text[idx:idx + 120]
        m = _MONEY_RE.search(seg)
        if m:
            v = _to_float(m.group(1))
            if v > 0:
                return v
    vals = [_to_float(g) for g in _MONEY_RE.findall(text)]
    return max(vals) if vals else 0.0


def _regex_find_tax(text):
    """「税额」关键词后紧邻的金额；找不到返回 0（税额识别主要靠 LLM）。"""
    for kw in ("税额", "税 额"):
        idx = text.find(kw)
        if idx == -1:
            continue
        seg = text[idx:idx + 60]
        m = _MONEY_RE.search(seg)
        if m:
            v = _to_float(m.group(1))
            if v > 0:
                return v
    return 0.0


def _regex_find_date(text):
    """「开票日期/填开日期/日期」关键词后第一个日期；找不到再全文兜底。"""
    for kw in ("开票日期", "填开日期", "销售日期", "开票时间", "日期", "时间"):
        idx = text.find(kw)
        if idx == -1:
            continue
        seg = text[idx:idx + 40]
        m = _DATE_RE.search(seg)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = _DATE_RE.search(text)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return ""


def _extract_financial_doc_fields(content: str, filename: str = "") -> dict:
    """用 DeepSeek 从财务单据（暂估单/结算单/发票）抽取 价税合计金额、税额、发生日期。
    LLM 漏识别时用正则从原始文本兜底。失败返回空 dict。"""
    if not content or not content.strip():
        return {}
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    text_part = content[:6000] + "\n……（中间省略）……\n" + content[-2000:]
    prompt = (
        "你是财务单据识别专家。请阅读下面这份财务单据（暂估单/结算单/发票）文本，抽取关键字段，只返回一个 JSON 对象：\n"
        "{\n"
        "  \"amount\": 0,\n"
        "  \"tax_amount\": 0,\n"
        "  \"doc_date\": \"\"\n"
        "}\n\n"
        "字段说明：\n"
        "- amount 价税合计金额（含税总额，纯数字）。发票上通常是「价税合计（小写）￥XXXX」或「（小写）¥XXXX」\n"
        "- tax_amount 税额（纯数字，没有填 0），发票上在「税额」列或「合计」行\n"
        "- doc_date 单据/发票发生日期（YYYY-MM-DD），发票上是「开票日期/填开日期」\n\n"
        "严格规则：\n"
        "1. 金额必须是纯数字，去掉元/货币符号/逗号/中文大写（\"壹佰万元整\"→1000000，\"100,000\"→100000，\"10万\"→100000）\n"
        "2. 价税合计优先；若只给出不含税金额与税率，请自行计算价税合计\n"
        "3. 日期统一 YYYY-MM-DD，没有就填空串\n"
        "4. 本段文本来自 OCR 识别，可能有个别错字、把「¥」识别成「Y」、把「日」识别成「日」等，结合上下文判断即可，不要因个别乱码放弃提取\n"
        "5. 只输出 JSON，禁止 markdown 代码块和任何解释\n\n"
        f"文件名：{filename}\n"
        f"单据文本：\n{text_part}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 600,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        data = {}
    else:
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            data = {}

    amount = _to_float(data.get("amount"))
    tax_amount = _to_float(data.get("tax_amount"))
    doc_date = str(data.get("doc_date") or "").strip()

    # 正则兜底：LLM 漏识别/失败时，从原始 OCR 文本按关键词二次提取
    if not amount:
        amount = _regex_find_amount(content)
    if not tax_amount:
        tax_amount = _regex_find_tax(content)
    if not doc_date:
        doc_date = _regex_find_date(content)

    return {"amount": amount, "tax_amount": tax_amount, "doc_date": doc_date}


def _fin_doc_to_dict(d: FinancialDoc) -> dict:
    return {
        "id": d.id,
        "contract_id": d.contract_id,
        "project_id": d.project_id,
        "doc_type": d.doc_type,
        "original_name": d.original_name,
        "amount": d.amount or 0,
        "tax_amount": d.tax_amount or 0,
        "amount_ex_tax": round((d.amount or 0) - (d.tax_amount or 0), 2),
        "doc_date": d.doc_date or "",
        "confirmed": d.confirmed or 0,
        "remark": d.remark or "",
        "created_at": d.created_at.strftime("%Y-%m-%d %H:%M:%S") if d.created_at else "",
        "download_url": f"/financial-docs/{d.id}/download",
    }


def _process_financial_doc_async(doc_id: int, file_path: str, original_name: str):
    """后台线程：提取单据文本 → LLM 抽取 金额/税额/日期 → 回填，置「待人工确认」。"""
    db = SessionLocal()
    try:
        content = ""
        try:
            content = extract_text_from_file(file_path)
            logger.info(f"[财务单据后台] OCR/提取文本长度 {len(content)}：{original_name}")
        except Exception as e:
            logger.error(f"[财务单据后台] 提取文本失败 {original_name}：{e}")

        fields = {}
        if content and content.strip():
            try:
                fields = _extract_financial_doc_fields(content, original_name)
            except Exception as e:
                logger.error(f"[财务单据后台] 抽取字段失败 {original_name}：{e}")

        d = db.query(FinancialDoc).filter(FinancialDoc.id == doc_id).first()
        if d is None:
            return
        d.amount = fields.get("amount") or None
        d.tax_amount = fields.get("tax_amount") or None
        d.doc_date = fields.get("doc_date") or None
        if not content or not content.strip():
            d.remark = "未能识别到文字（可能为模糊照片/扫描件），请人工填写金额/税额/日期"
        elif not d.amount and not d.tax_amount and not d.doc_date:
            d.remark = "已识别文字但未提取到金额/税额/日期，请人工校对"
        else:
            d.remark = "LLM 自动提取，待人工确认"
        db.commit()
        logger.info(f"[财务单据后台] 解析完成 {original_name}（金额={d.amount} 税额={d.tax_amount} 日期={d.doc_date}）")
    except Exception as e:
        logger.error(f"[财务单据后台] 处理异常 {original_name}：{e}")
        try:
            d = db.query(FinancialDoc).filter(FinancialDoc.id == doc_id).first()
            if d is not None:
                d.remark = f"解析失败：{e}"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@app.post("/financial-docs", summary="上传财务资料（暂估单/结算单/发票），后台自动提取金额/税额/日期")
async def upload_financial_docs(
    files: List[UploadFile] = File(...),
    contract_id: int = Form(...),
    doc_type: str = Form(...),
    project_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="关联合同不存在")
    if doc_type not in FINANCIAL_DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"单据类型须为 {FINANCIAL_DOC_TYPES} 之一")
    pid = project_id if project_id is not None else c.project_id
    results, errors = [], []
    for f in files:
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
            ext = os.path.splitext(f.filename)[1]
            stored = f"fin_{ts}{ext}"
            path = os.path.join(UPLOAD_DIR, stored)
            with open(path, "wb") as buf:
                shutil.copyfileobj(f.file, buf)
            d = FinancialDoc(
                contract_id=contract_id, project_id=pid, doc_type=doc_type,
                original_name=f.filename, stored_name=stored, file_path=path,
                confirmed=0, remark="解析中…",
            )
            db.add(d)
            db.commit()
            db.refresh(d)
            _ocr_executor.submit(_process_financial_doc_async, d.id, path, f.filename)
            results.append(_fin_doc_to_dict(d))
        except Exception as e:
            db.rollback()
            logger.error(f"[财务单据] 上传失败 {f.filename}：{e}")
            errors.append({"filename": f.filename, "error": str(e)})
    return {"uploaded": results, "errors": errors}


@app.get("/financial-docs", summary="财务资料列表（可按合同/项目/类型筛选）")
def list_financial_docs(
    contract_id: Optional[int] = None,
    project_id: Optional[int] = None,
    doc_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(FinancialDoc)
    if contract_id is not None:
        q = q.filter(FinancialDoc.contract_id == contract_id)
    if project_id is not None:
        q = q.filter(FinancialDoc.project_id == project_id)
    if doc_type:
        q = q.filter(FinancialDoc.doc_type == doc_type)
    items = q.order_by(FinancialDoc.created_at.desc()).all()
    return [_fin_doc_to_dict(d) for d in items]


class FinancialDocUpdate(BaseModel):
    amount: Optional[float] = None
    tax_amount: Optional[float] = None
    doc_date: Optional[str] = None
    doc_type: Optional[str] = None
    remark: Optional[str] = None


@app.put("/financial-docs/{doc_id}", summary="编辑财务资料字段（人工校对修正）")
def update_financial_doc(doc_id: int, req: FinancialDocUpdate, db: Session = Depends(get_db)):
    d = db.query(FinancialDoc).filter(FinancialDoc.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="财务资料不存在")
    for k, v in req.dict(exclude_unset=True).items():
        setattr(d, k, v)
    db.commit()
    return _fin_doc_to_dict(d)


@app.post("/financial-docs/{doc_id}/confirm", summary="财务资料人工确认（识别结果经人工校对后生效）")
def confirm_financial_doc(doc_id: int, db: Session = Depends(get_db)):
    d = db.query(FinancialDoc).filter(FinancialDoc.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="财务资料不存在")
    d.confirmed = 1
    d.remark = (d.remark or "") + "；已人工确认"
    db.commit()
    return _fin_doc_to_dict(d)


@app.get("/financial-docs/{doc_id}/download", summary="下载财务资料文件")
def download_financial_doc(doc_id: int, db: Session = Depends(get_db)):
    d = db.query(FinancialDoc).filter(FinancialDoc.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="财务资料不存在")
    if not os.path.exists(d.file_path):
        raise HTTPException(status_code=404, detail="文件在磁盘上不存在")
    return FileResponse(path=d.file_path, filename=d.original_name, media_type="application/octet-stream")


@app.delete("/financial-docs/{doc_id}", summary="删除财务资料")
def delete_financial_doc(doc_id: int, db: Session = Depends(get_db)):
    d = db.query(FinancialDoc).filter(FinancialDoc.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="财务资料不存在")
    if os.path.exists(d.file_path):
        try:
            os.remove(d.file_path)
        except Exception:
            pass
    db.delete(d)
    db.commit()
    return {"message": f"财务资料「{d.original_name}」已删除"}


# ============ 系统可配置项（待定口径） ============
def _get_config(db: Session, key: str, default: str = "") -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if (row and row.value is not None) else default


@app.get("/config", summary="读取系统可配置项（最终金额口径/超额基准/延期基准/容忍比例）")
def get_config(db: Session = Depends(get_db)):
    rows = db.query(SystemConfig).all()
    data = {r.key: r.value for r in rows}
    for k, v in _DEFAULT_CONFIGS.items():
        data.setdefault(k, v)
    return data


class ConfigUpdateRequest(BaseModel):
    config: dict


@app.put("/config", summary="更新系统可配置项")
def update_config(req: ConfigUpdateRequest, db: Session = Depends(get_db)):
    for k, v in (req.config or {}).items():
        if k not in _DEFAULT_CONFIGS:
            continue
        row = db.query(SystemConfig).filter(SystemConfig.key == k).first()
        if row:
            row.value = str(v)
            row.updated_at = datetime.utcnow()
        else:
            db.add(SystemConfig(key=k, value=str(v)))
    db.commit()
    return get_config(db)


# ============ 经费比对 / 风险预警 / 预算统计 ============
def _funding_overview(db: Session, project_id: Optional[int] = None) -> dict:
    """经费全景：按项目聚合合同含税额 + 暂估/结算/发票实际额，计算超额与差异。
    final_amount_basis 决定「最终填充金额」口径；发票默认覆盖暂估/结算。"""
    cfg = {
        "final_amount_basis": _get_config(db, "final_amount_basis", "invoice"),
        "overrun_basis": _get_config(db, "overrun_basis", "contract_incl_tax"),
        "overrun_tolerance": float(_get_config(db, "overrun_tolerance", "0.0") or 0),
    }
    pmap = {p.id: p.name for p in db.query(Project).all()}
    q = db.query(Contract)
    if project_id is not None:
        q = q.filter(Contract.project_id == project_id)
    contracts = q.order_by(Contract.created_at.asc(), Contract.id.asc()).all()
    cids = [c.id for c in contracts]
    docs = db.query(FinancialDoc).filter(FinancialDoc.contract_id.in_(cids)).all() if cids else []

    doc_map: dict = {}
    for d in docs:
        doc_map.setdefault(d.contract_id, []).append(d)

    contracts_detail = []
    projects_agg: dict = {}
    for c in contracts:
        dl = doc_map.get(c.id, [])
        est = sum(d.amount or 0 for d in dl if d.doc_type == "暂估单" and d.confirmed)
        stl = sum(d.amount or 0 for d in dl if d.doc_type == "结算单" and d.confirmed)
        inv = sum(d.amount or 0 for d in dl if d.doc_type == "发票" and d.confirmed)
        incl = c.amount_incl_tax or c.amount_value or 0
        basis = cfg["final_amount_basis"]
        if basis == "estimate":
            eff = est
        elif basis == "both":
            eff = None
        else:  # invoice 默认：发票覆盖暂估/结算
            eff = inv if inv > 0 else (stl if stl > 0 else est)
        diff = (eff - incl) if eff is not None else None
        contracts_detail.append({
            "id": c.id, "contract_name": c.contract_name or "", "original_name": c.original_name or "",
            "contract_no": c.contract_no or "",
            "party_a": c.party_a or "", "party_b": c.party_b or "",
            "project_id": c.project_id, "project_name": pmap.get(c.project_id, ""),
            "amount_incl_tax": round(incl, 2), "amount_ex_tax": round(c.amount_ex_tax or 0, 2),
            "tax_amount": round(c.tax_amount or 0, 2),
            "sign_date": c.sign_date or "", "due_date": c.due_date or "",
            "warranty_ratio": c.warranty_ratio or 0, "warranty_due_date": c.warranty_due_date or "",
            "estimate_total": round(est, 2), "settlement_total": round(stl, 2), "invoice_total": round(inv, 2),
            "effective_total": round(eff, 2) if eff is not None else None,
            "diff_vs_contract": round(diff, 2) if diff is not None else None,
        })
        pid = c.project_id if c.project_id is not None else 0
        a = projects_agg.setdefault(pid, {
            "project_id": pid, "project_name": pmap.get(pid, "平台级"),
            "contract_incl_tax": 0.0, "estimate": 0.0, "settlement": 0.0, "invoice": 0.0,
            "effective": 0.0, "contract_count": 0,
        })
        a["contract_incl_tax"] += incl
        a["estimate"] += est
        a["settlement"] += stl
        a["invoice"] += inv
        a["effective"] += (eff or 0)
        a["contract_count"] += 1

    budget_map: dict = {}
    for f in db.query(Funding).filter(Funding.fund_type == "预算").all():
        budget_map[f.project_id] = budget_map.get(f.project_id, 0.0) + (f.amount or 0)

    projects_list = []
    for pid, a in projects_agg.items():
        baseline = a["contract_incl_tax"] if cfg["overrun_basis"] == "contract_incl_tax" else budget_map.get(pid, 0.0)
        tolerance = cfg["overrun_tolerance"]
        diff = a["effective"] - baseline
        overrun = diff > baseline * tolerance
        progress = round(a["effective"] / baseline * 100, 1) if baseline else None
        projects_list.append({
            **a,
            "budget": round(budget_map.get(pid, 0.0), 2),
            "baseline": round(baseline, 2),
            "diff": round(diff, 2),
            "overrun": bool(overrun),
            "progress_pct": progress,
        })
    projects_list.sort(key=lambda x: -x["effective"])

    totals = {
        "contract_incl_tax": round(sum(a["contract_incl_tax"] for a in projects_agg.values()), 2),
        "estimate": round(sum(a["estimate"] for a in projects_agg.values()), 2),
        "settlement": round(sum(a["settlement"] for a in projects_agg.values()), 2),
        "invoice": round(sum(a["invoice"] for a in projects_agg.values()), 2),
        "effective": round(sum(a["effective"] for a in projects_agg.values()), 2),
    }
    return {"config": cfg, "projects": projects_list, "contracts": contracts_detail, "totals": totals}


@app.get("/funding/compare", summary="经费比对：选项目带出合同金额/日期，发票覆盖暂估结算，累计实际额 vs 合同含税额")
def funding_compare(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    overview = _funding_overview(db, project_id)
    overview["alerts"] = [
        it for it in _generate_todo_items(db)
        if project_id is None or it.get("project_id") == project_id
    ]
    return overview


@app.get("/funding/alerts", summary="风险预警：超额/逾期付款/延期/质保金到期/合同超期临期/节点待办")
def funding_alerts(db: Session = Depends(get_db)):
    items = _generate_todo_items(db)
    _sync_todos(db)
    return {"alerts": items, "count": len(items)}


class AlertDismissRequest(BaseModel):
    alert_key: str
    category: Optional[str] = None


@app.post("/funding/alerts/dismiss", summary="单条预警不再提醒（幂等）")
def dismiss_alert(req: AlertDismissRequest, db: Session = Depends(get_db)):
    if not req.alert_key:
        raise HTTPException(status_code=400, detail="alert_key 不能为空")
    db.execute(
        text("INSERT OR IGNORE INTO alert_dismissed (alert_key, category, dismissed_at, operator) "
             "VALUES (:k, :c, datetime('now'), 'admin')"),
        {"k": req.alert_key, "c": req.category or ""},
    )
    db.commit()
    _sync_todos(db)
    return {"message": "已不再提醒"}


@app.post("/funding/alerts/dismiss-expired", summary="批量清理已过期提醒（alert_date 早于今天）")
def dismiss_expired_alerts(db: Session = Depends(get_db)):
    items = _generate_todo_items(db)
    today = date.today()
    count = 0
    for it in items:
        ad = it.get("alert_date")
        if not ad:
            continue
        d = _parse_date(ad)
        if d and d < today:
            db.execute(
                text("INSERT OR IGNORE INTO alert_dismissed (alert_key, category, dismissed_at, operator) "
                     "VALUES (:k, :c, datetime('now'), 'admin')"),
                {"k": it["alert_key"], "c": it["category"]},
            )
            count += 1
    db.commit()
    _sync_todos(db)
    return {"dismissed_count": count}


def _generate_todo_items(db: Session) -> list:
    """汇总所有提醒为待办条目（不写库，纯计算）。alert_key 为稳定键，供 _sync_todos 幂等同步与 dismissed 过滤。"""
    today = date.today()
    pmap = {p.id: p.name for p in db.query(Project).all()}
    contracts = db.query(Contract).all()
    cmap = {c.id: c for c in contracts}
    nodes = db.query(ContractNode).all()
    docs = db.query(FinancialDoc).all()
    items: list = []

    def _cname(cid):
        c = cmap.get(cid)
        return (c.contract_name or c.original_name) if c else f"合同#{cid}"

    # 1) 合同超期 / 临期（已忽略预警的合同不再提示）
    for c in contracts:
        if c.alert_ignored:
            continue
        status, days_left = _contract_status(c.due_date)
        if status == "已超期":
            items.append({"category": "合同超期", "level": "danger",
                          "title": f"合同「{c.contract_name or c.original_name}」已超期",
                          "detail": f"到期日 {c.due_date}，已超 {abs(days_left)} 天", "link": "contracts",
                          "source_id": c.id, "project_id": c.project_id, "contract_id": c.id,
                          "alert_key": f"合同超期|{c.id}", "alert_date": c.due_date})
        elif status == "即将到期":
            items.append({"category": "合同临期", "level": "warning",
                          "title": f"合同「{c.contract_name or c.original_name}」即将到期",
                          "detail": f"到期日 {c.due_date}，剩 {days_left} 天", "link": "contracts",
                          "source_id": c.id, "project_id": c.project_id, "contract_id": c.id,
                          "alert_key": f"合同临期|{c.id}", "alert_date": c.due_date})

    # 2) 付款节点逾期 / 临近；业务节点（验收、进度报告）到期 / 临近
    for n in nodes:
        if n.status in ("已完成", "已取消"):
            continue
        if not n.planned_date:
            continue
        d = _parse_date(n.planned_date)
        if not d:
            continue
        days = (d - today).days
        cname = _cname(n.contract_id)
        c = cmap.get(n.contract_id)
        if n.node_type == "payment":
            if days < 0:
                items.append({"category": "逾期付款", "level": "danger",
                              "title": f"付款节点「{n.node_name}」已逾期",
                              "detail": f"{cname} 约定 {n.planned_date}，已逾期 {abs(days)} 天",
                              "link": "contracts", "source_id": n.id,
                              "project_id": c.project_id if c else None, "contract_id": n.contract_id,
                              "alert_key": f"逾期付款|{n.id}", "alert_date": n.planned_date})
            elif days <= 7:
                items.append({"category": "节点待办", "level": "warning",
                              "title": f"付款节点「{n.node_name}」临近",
                              "detail": f"{cname} 约定 {n.planned_date}，剩 {days} 天",
                              "link": "contracts", "source_id": n.id,
                              "project_id": c.project_id if c else None, "contract_id": n.contract_id,
                              "alert_key": f"节点待办|{n.id}", "alert_date": n.planned_date})
        else:
            if days < 0:
                items.append({"category": "节点待办", "level": "danger",
                              "title": f"业务节点「{n.node_name}」已到期待办",
                              "detail": f"{cname} 约定 {n.planned_date}，请处理（验收/进度报告等）",
                              "link": "contracts", "source_id": n.id,
                              "project_id": c.project_id if c else None, "contract_id": n.contract_id,
                              "alert_key": f"节点待办|{n.id}", "alert_date": n.planned_date})
            elif days <= 7:
                items.append({"category": "节点待办", "level": "info",
                              "title": f"业务节点「{n.node_name}」临近",
                              "detail": f"{cname} 约定 {n.planned_date}，剩 {days} 天",
                              "link": "contracts", "source_id": n.id,
                              "project_id": c.project_id if c else None, "contract_id": n.contract_id,
                              "alert_key": f"节点待办|{n.id}", "alert_date": n.planned_date})

    # 3) 质保金到期
    for c in contracts:
        if not c.warranty_due_date or not (c.warranty_ratio and c.warranty_ratio > 0):
            continue
        d = _parse_date(c.warranty_due_date)
        if not d:
            continue
        days = (d - today).days
        if days <= 30:
            level = "danger" if days < 0 else "warning"
            items.append({"category": "质保金到期", "level": level,
                          "title": f"质保金到期：{c.contract_name or c.original_name}",
                          "detail": f"质保金比例 {round((c.warranty_ratio or 0) * 100, 1)}%，到期日 {c.warranty_due_date}",
                          "link": "contracts", "source_id": c.id,
                          "project_id": c.project_id, "contract_id": c.id,
                          "alert_key": f"质保金到期|{c.id}", "alert_date": c.warranty_due_date})

    # 4) 延期报警（发票/付款日期晚于合同约定交付日期）
    for fd in docs:
        if fd.doc_type != "发票" or not fd.confirmed or not fd.doc_date:
            continue
        c = cmap.get(fd.contract_id)
        if not c:
            continue
        due = c.due_date or c.end_date
        dd = _parse_date(fd.doc_date)
        bd = _parse_date(due)
        if dd and bd and dd > bd:
            items.append({"category": "延期", "level": "warning",
                          "title": f"发票日期晚于合同约定：{fd.original_name}",
                          "detail": f"发票日期 {fd.doc_date}，合同约定 {due}",
                          "link": "funding", "source_id": fd.id,
                          "project_id": c.project_id, "contract_id": c.id,
                          "alert_key": f"延期|{fd.id}", "alert_date": due})

    # 5) 超额报警
    overview = _funding_overview(db)
    for p in overview["projects"]:
        if p["overrun"]:
            items.append({"category": "超额", "level": "danger",
                          "title": f"项目「{p['project_name']}」累计实际额超合同含税额",
                          "detail": f"实际 {p['effective']}，基准 {p['baseline']}，超出 {p['diff']}",
                          "link": "funding", "source_id": p["project_id"],
                          "project_id": p["project_id"], "contract_id": None,
                          "alert_key": f"超额|{p['project_id']}", "alert_date": None})

    # 统一过滤：已关闭（不再提醒）的预警不返回 → 预警/待办/角标三处一致
    dismissed_keys = {d.alert_key for d in db.query(AlertDismissed).all()}
    return [it for it in items if it["alert_key"] not in dismissed_keys]


def _sync_todos(db: Session):
    """把计算出的提醒幂等同步到 todos 表：新增未处理项、刷新文本但保留「已处理」状态、清理已消失项。"""
    items = _generate_todo_items(db)
    existing = {f"{t.category}|{t.source_id}": t for t in db.query(Todo).all()}
    seen = set()
    for it in items:
        key = it["alert_key"]
        seen.add(key)
        t = existing.get(key)
        if t is None:
            db.add(Todo(category=it["category"], level=it["level"], title=it["title"],
                        detail=it["detail"], link=it["link"], source_id=it["source_id"],
                        status="未处理"))
        else:
            t.title = it["title"]
            t.detail = it["detail"]
            t.level = it["level"]
            t.link = it["link"]
    for key, t in existing.items():
        if key not in seen:
            db.delete(t)
    db.commit()


def _todo_to_dict(t: Todo) -> dict:
    return {
        "id": t.id, "category": t.category, "level": t.level,
        "title": t.title, "detail": t.detail or "", "link": t.link or "",
        "source_id": t.source_id, "status": t.status or "未处理",
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
        "handled_at": t.handled_at.strftime("%Y-%m-%d %H:%M:%S") if t.handled_at else "",
    }


# ============ 待办模块 ============
@app.get("/todos", summary="待办列表（统一汇总所有提醒，自动刷新）")
def list_todos(status: Optional[str] = None, category: Optional[str] = None, db: Session = Depends(get_db)):
    _sync_todos(db)
    q = db.query(Todo)
    if status:
        q = q.filter(Todo.status == status)
    if category:
        q = q.filter(Todo.category == category)
    items = q.order_by(Todo.status.asc(), Todo.created_at.desc()).all()
    unhandled = db.query(Todo).filter(Todo.status == "未处理").count()
    return {"todos": [_todo_to_dict(t) for t in items], "unhandled": unhandled}


@app.get("/todos/count", summary="待办未处理数量（导航栏角标）")
def todo_count(db: Session = Depends(get_db)):
    _sync_todos(db)
    unhandled = db.query(Todo).filter(Todo.status == "未处理").count()
    return {"unhandled": unhandled}


@app.post("/todos/refresh", summary="重新生成待办提醒")
def refresh_todos(db: Session = Depends(get_db)):
    _sync_todos(db)
    unhandled = db.query(Todo).filter(Todo.status == "未处理").count()
    return {"unhandled": unhandled}


@app.post("/todos/{todo_id}/handle", summary="标记待办已处理")
def handle_todo(todo_id: int, db: Session = Depends(get_db)):
    t = db.query(Todo).filter(Todo.id == todo_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="待办不存在")
    t.status = "已处理"
    t.handled_at = datetime.utcnow()
    db.commit()
    return _todo_to_dict(t)


@app.post("/todos/handle-all", summary="一键标记全部待办已处理")
def handle_all_todos(db: Session = Depends(get_db)):
    _sync_todos(db)
    rows = db.query(Todo).filter(Todo.status == "未处理").all()
    for t in rows:
        t.status = "已处理"
        t.handled_at = datetime.utcnow()
    db.commit()
    return {"handled": len(rows)}


# ============ 预算与统计 ============
def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


@app.get("/funding/budget", summary="预算与统计：下月/下季度预计付款 + 多维统计（支持时间筛选）")
def funding_budget(start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    today = date.today()
    next_month_start = _add_months(today, 1)
    next_month_end = _add_months(today, 2) - timedelta(days=1)
    quarter_start = _add_months(today, 1)
    quarter_end = _add_months(today, 4) - timedelta(days=1)

    cmap = {c.id: c for c in db.query(Contract).all()}
    pmap = {p.id: p.name for p in db.query(Project).all()}

    next_month_items, next_quarter_items = [], []
    nodes = db.query(ContractNode).filter(ContractNode.node_type == "payment").all()
    for n in nodes:
        if n.status in ("已完成", "已取消"):
            continue
        d = _parse_date(n.planned_date)
        if not d:
            continue
        c = cmap.get(n.contract_id)
        rec = {
            "node_id": n.id, "node_name": n.node_name, "planned_date": n.planned_date,
            "amount": n.amount or 0,
            "contract_name": (c.contract_name or c.original_name) if c else f"合同#{n.contract_id}",
            "project_name": pmap.get(c.project_id, "") if c else "",
        }
        if next_month_start <= d <= next_month_end:
            next_month_items.append(rec)
        if quarter_start <= d <= quarter_end:
            next_quarter_items.append(rec)

    # 多维统计：按时间（月/季/年）、按合同、按项目、按责任方（乙方/合作方）
    docs = db.query(FinancialDoc).filter(FinancialDoc.confirmed == 1).all()
    if start or end:
        docs = [d for d in docs if _date_in_range(d.doc_date, start, end)]
    by_month, by_quarter, by_year = {}, {}, {}
    for d in docs:
        if not d.doc_date:
            continue
        dt = _parse_date(d.doc_date)
        if not dt:
            continue
        amt = d.amount or 0
        mk = f"{dt.year}-{dt.month:02d}"
        by_month[mk] = round(by_month.get(mk, 0) + amt, 2)
        by_year[str(dt.year)] = round(by_year.get(str(dt.year), 0) + amt, 2)
        qk = f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
        by_quarter[qk] = round(by_quarter.get(qk, 0) + amt, 2)

    overview = _funding_overview(db)
    by_contract = {c["contract_name"] or f"合同#{c['id']}": c["effective_total"] or 0 for c in overview["contracts"]}
    by_project = {p["project_name"]: p["effective"] for p in overview["projects"]}
    by_party = {}
    for c in overview["contracts"]:
        party = c["party_b"] or "未识别合作方"
        by_party[party] = round(by_party.get(party, 0) + (c["effective_total"] or 0), 2)

    return {
        "next_month": {"start": next_month_start.strftime("%Y-%m-%d"), "end": next_month_end.strftime("%Y-%m-%d"),
                       "total": round(sum(i["amount"] for i in next_month_items), 2), "items": next_month_items},
        "next_quarter": {"start": quarter_start.strftime("%Y-%m-%d"), "end": quarter_end.strftime("%Y-%m-%d"),
                         "total": round(sum(i["amount"] for i in next_quarter_items), 2), "items": next_quarter_items},
        "by_month": by_month, "by_quarter": by_quarter, "by_year": by_year,
        "by_contract": by_contract, "by_project": by_project, "by_party": by_party,
    }


@app.get("/financial-docs/year-summary", summary="跨年归属：按资料发生日期归属年度，附合同总额与执行进度")
def financial_docs_year_summary(db: Session = Depends(get_db)):
    pmap = {p.id: p.name for p in db.query(Project).all()}
    docs = db.query(FinancialDoc).filter(FinancialDoc.confirmed == 1).all()
    # (project_id, year) -> {estimate, settlement, invoice, total}
    agg: dict = {}
    for d in docs:
        if not d.doc_date:
            continue
        dt = _parse_date(d.doc_date)
        if not dt:
            continue
        key = (d.project_id or 0, dt.year)
        a = agg.setdefault(key, {"estimate": 0.0, "settlement": 0.0, "invoice": 0.0, "total": 0.0})
        amt = d.amount or 0
        if d.doc_type == "暂估单":
            a["estimate"] += amt
        elif d.doc_type == "结算单":
            a["settlement"] += amt
        else:
            a["invoice"] += amt
        a["total"] += amt

    year_projects = {}
    for (pid, year), a in agg.items():
        year_projects.setdefault(year, []).append({
            "project_id": pid, "project_name": pmap.get(pid, "平台级"),
            "estimate": round(a["estimate"], 2), "settlement": round(a["settlement"], 2),
            "invoice": round(a["invoice"], 2), "total": round(a["total"], 2),
        })

    overview = _funding_overview(db)
    return {
        "years": {str(y): {"projects": v, "total": round(sum(x["total"] for x in v), 2)}
                  for y, v in sorted(year_projects.items())},
        "contract_incl_tax_total": overview["totals"]["contract_incl_tax"],
        "effective_total": overview["totals"]["effective"],
        "progress_pct": round(overview["totals"]["effective"] / overview["totals"]["contract_incl_tax"] * 100, 1)
        if overview["totals"]["contract_incl_tax"] else None,
    }


# ============ 成果台账 ============
ACHIEVEMENT_CATEGORIES = ["专利", "论文", "软件著作权", "获奖", "标准", "成果登记", "鉴定报告", "其他"]
ACHIEVEMENT_STATUSES = ["在研", "已授权", "已发表", "已登记", "已获奖", "已发布", "其他"]


def _extract_achievement_fields(content: str, filename: str = "") -> dict:
    """用 DeepSeek 从成果文件文本识别：名称/类型/状态/权利人/日期/备注。失败返回空 dict。"""
    if not content or not content.strip():
        return {}
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    text_part = content[:6000] + "\n……（中间省略）……\n" + content[-1500:]
    prompt = (
        "你是科技成果信息抽取专家。请阅读下面这份成果材料（专利证书/论文/软件著作权证书/获奖证书/标准等），"
        "抽取关键字段，只返回一个 JSON 对象：\n"
        "{\n"
        '  "name": "成果名称（专利名/论文题目/软著名/奖项名等）",\n'
        '  "category": "类型，从[专利,论文,软件著作权,获奖,标准,成果登记,鉴定报告,其他]选一个",\n'
        '  "status": "状态，从[在研,已授权,已发表,已登记,已获奖,已发布,其他]选一个",\n'
        '  "holder": "权利人/作者/完成人/专利权人",\n'
        '  "achieve_date": "取得日期（授权日/发表日期/获奖日期等），YYYY-MM-DD",\n'
        '  "application_date": "申请日期（专利/软著等证书上标注的申请日），YYYY-MM-DD，没有则填空串",\n'
        '  "remark": "备注（专利号/刊物/等级/编号等补充信息，可简略）"\n'
        "}\n\n"
        "严格规则：\n"
        "1. 只输出 JSON，禁止 markdown 代码块和任何解释\n"
        "2. 日期统一 YYYY-MM-DD，没有就填空串\n"
        "3. category/status 必须从给定候选里选最贴切的一个\n"
        "4. 无法判断的字段填空串\n\n"
        f"文件名：{filename}\n"
        f"材料文本：\n{text_part}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    return {
        "name": str(data.get("name") or "").strip(),
        "category": str(data.get("category") or "").strip(),
        "status": str(data.get("status") or "").strip(),
        "holder": str(data.get("holder") or "").strip(),
        "achieve_date": str(data.get("achieve_date") or "").strip(),
        "application_date": str(data.get("application_date") or "").strip(),
        "remark": str(data.get("remark") or "").strip(),
    }


def _process_achievement_async(achievement_id: int, file_path: str, original_name: str):
    """后台线程：提取成果文件文本（含扫描件 OCR）→ LLM 识别字段 → 回填数据库，最后置 done/error。"""
    db = SessionLocal()
    try:
        content = ""
        try:
            content = extract_text_from_file(file_path)
        except Exception as e:
            logger.error(f"[成果后台] 提取文本失败 {original_name}：{e}")

        fields = {}
        if content and content.strip():
            try:
                fields = _extract_achievement_fields(content, original_name)
            except Exception as e:
                logger.error(f"[成果后台] 识别字段失败 {original_name}：{e}")

        a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
        if a is None:
            return
        a.name = fields.get("name") or None
        a.category = fields.get("category") or "其他"
        a.status = fields.get("status") or "其他"
        a.holder = fields.get("holder") or None
        a.achieve_date = fields.get("achieve_date") or None
        a.application_date = fields.get("application_date") or None
        a.remark = fields.get("remark") or None
        a.processing_status = "done"
        db.commit()
        logger.info(f"[成果后台] 识别完成 {original_name}")
    except Exception as e:
        logger.error(f"[成果后台] 处理异常 {original_name}：{e}")
        try:
            a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
            if a is not None:
                a.processing_status = "error"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _reidentify_achievement_async(achievement_id: int, file_path: str, original_name: str):
    """后台线程：重新识别已有成果文件，回填缺失字段（重点补「申请日期」，不覆盖已有值）。"""
    db = SessionLocal()
    try:
        content = ""
        try:
            content = extract_text_from_file(file_path)
        except Exception as e:
            logger.error(f"[成果重识别] 提取文本失败 {original_name}：{e}")

        fields = {}
        if content and content.strip():
            try:
                fields = _extract_achievement_fields(content, original_name)
            except Exception as e:
                logger.error(f"[成果重识别] 识别字段失败 {original_name}：{e}")

        a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
        if a is None:
            return

        # 申请日期：识别到非空就覆盖（本次重识别的核心目标）
        new_app_date = (fields.get("application_date") or "").strip()
        if new_app_date:
            a.application_date = new_app_date

        # 其余字段仅在当前为空时回填，避免覆盖已登记/已修正的值
        for attr, key in [
            ("name", "name"),
            ("category", "category"),
            ("status", "status"),
            ("holder", "holder"),
            ("achieve_date", "achieve_date"),
            ("remark", "remark"),
        ]:
            val = (fields.get(key) or "").strip()
            if val and not getattr(a, attr, None):
                setattr(a, attr, val)

        a.processing_status = "done"
        db.commit()
        logger.info(f"[成果重识别] 完成 {original_name}")
    except Exception as e:
        logger.error(f"[成果重识别] 处理异常 {original_name}：{e}")
        try:
            a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
            if a is not None:
                a.processing_status = "error"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


class AchievementCreate(BaseModel):
    project_id: Optional[int] = None
    name: str
    category: Optional[str] = "其他"
    status: Optional[str] = "其他"
    holder: Optional[str] = None
    application_date: Optional[str] = None
    achieve_date: Optional[str] = None
    remark: Optional[str] = None


class AchievementUpdate(BaseModel):
    """成果编辑（整体更新）：project_id=None 表示平台级；name 必填。"""
    project_id: Optional[int] = None
    name: str
    category: str = "其他"
    status: str = "其他"
    holder: Optional[str] = None
    application_date: Optional[str] = None
    achieve_date: Optional[str] = None
    remark: Optional[str] = None


def _achievement_to_dict(a: Achievement, project_name: str = "") -> dict:
    return {
        "id": a.id,
        "project_id": a.project_id,
        "project_name": project_name,
        "name": a.name or "",
        "category": a.category or "其他",
        "status": a.status or "其他",
        "holder": a.holder or "",
        "application_date": a.application_date or "",
        "achieve_date": a.achieve_date or "",
        "remark": a.remark or "",
        "file_name": a.file_name or "",
        "processing_status": a.processing_status or "done",
        "source": a.source or "manual",
        "source_file_id": a.source_file_id,
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
        "download_url": f"/achievements/{a.id}/download" if a.file_path else "",
    }


@app.post("/achievements", summary="新增成果")
def create_achievement(req: AchievementCreate, db: Session = Depends(get_db)):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="成果名称必填")
    a = Achievement(
        project_id=req.project_id,
        name=req.name.strip(),
        category=req.category or "其他",
        status=req.status or "其他",
        holder=req.holder or None,
        application_date=req.application_date or None,
        achieve_date=req.achieve_date or None,
        remark=req.remark or None,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    _audit("admin", "POST /achievements", f"新增成果：{a.name}")
    return _achievement_to_dict(a)


@app.post("/achievements/manual", summary="手动登记成果（可选上传附件并绑定）")
async def create_achievement_manual(
    name: str = Form(...),
    category: str = Form("其他"),
    status: str = Form("其他"),
    holder: Optional[str] = Form(None),
    application_date: Optional[str] = Form(None),
    achieve_date: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """手动登记成果：可附带一个附件文件，附件保存并与该成果记录绑定（不做自动识别）。"""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="成果名称必填")
    file_name = None
    file_path = None
    if file is not None and getattr(file, "filename", ""):
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            ext = os.path.splitext(file.filename)[1]
            stored_name = f"achievement_{timestamp}{ext}"
            file_path = os.path.join(UPLOAD_DIR, stored_name)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            file_name = file.filename
        except Exception as e:
            logger.error(f"[成果] 附件保存失败 {getattr(file, 'filename', '')}：{e}")
            raise HTTPException(status_code=500, detail=f"附件保存失败：{e}")

    a = Achievement(
        project_id=project_id,
        name=name.strip(),
        category=category or "其他",
        status=status or "其他",
        holder=holder or None,
        application_date=application_date or None,
        achieve_date=achieve_date or None,
        remark=remark or None,
        file_name=file_name,
        file_path=file_path,
        processing_status="done",
        source="manual",
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    _audit("admin", "POST /achievements/manual", f"手动登记成果：{a.name}" + (f"（附件 {file_name}）" if file_name else ""))
    pmap = {p.id: p.name for p in db.query(Project).all()}
    return _achievement_to_dict(a, pmap.get(a.project_id, ""))


@app.post("/achievements/upload", summary="批量上传成果文件（后台自动识别，可编辑修正）")
async def upload_achievement(
    files: List[UploadFile] = File(...),
    project_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """批量上传成果文件：逐个保存文件 → 建记录(processing) → 后台识别字段，识别结果可后续编辑修正。"""
    results = []
    errors = []
    pmap = {p.id: p.name for p in db.query(Project).all()}
    for file in files:
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            ext = os.path.splitext(file.filename)[1]
            stored_name = f"achievement_{timestamp}{ext}"
            file_path = os.path.join(UPLOAD_DIR, stored_name)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            a = Achievement(
                project_id=project_id,
                name=None,
                category=None,
                status=None,
                file_name=file.filename,
                file_path=file_path,
                processing_status="processing",
                source="auto",
            )
            db.add(a)
            db.commit()
            db.refresh(a)

            _ocr_executor.submit(_process_achievement_async, a.id, file_path, file.filename)
            _audit("admin", "POST /achievements/upload", f"上传成果文件：{file.filename}")
            results.append(_achievement_to_dict(a, pmap.get(a.project_id, "")))
        except Exception as e:
            db.rollback()
            logger.error(f"[成果] 上传失败 {file.filename}：{e}")
            errors.append({"filename": file.filename, "error": str(e)})

    return {"uploaded": results, "errors": errors}


class AchievementReidentifyRequest(BaseModel):
    ids: Optional[List[int]] = None  # 为空表示全量重跑


@app.post("/achievements/reidentify", summary="批量重新识别成果（后台重新提取证书申请日期等字段）")
def reidentify_achievements(req: AchievementReidentifyRequest, db: Session = Depends(get_db)):
    """遍历已有成果，对有磁盘文件的记录后台重新提取文本+LLM识别，重点回填「申请日期」。"""
    q = db.query(Achievement)
    if req.ids:
        q = q.filter(Achievement.id.in_(req.ids))
    items = q.all()

    tasks = []   # (id, file_path, file_name)
    skipped = []  # 无文件或文件不存在，无法重识别
    for a in items:
        if not a.file_path or not os.path.exists(a.file_path):
            skipped.append(a.id)
            continue
        a.processing_status = "processing"
        tasks.append((a.id, a.file_path, a.file_name or ""))
    db.commit()  # 先持久化 processing 状态，再提交后台任务，避免竞态

    for aid, fpath, fname in tasks:
        _ocr_executor.submit(_reidentify_achievement_async, aid, fpath, fname)

    _audit("admin", "POST /achievements/reidentify",
           f"批量重新识别 {len(tasks)} 条成果（跳过 {len(skipped)} 条无文件）")
    return {"triggered": len(tasks), "skipped": skipped, "count": len(tasks)}


@app.put("/achievements/{achievement_id}", summary="编辑成果（自由修正识别结果）")
def update_achievement(achievement_id: int, req: AchievementUpdate, db: Session = Depends(get_db)):
    a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="成果不存在")
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="成果名称必填")
    a.project_id = req.project_id
    a.name = req.name.strip()
    a.category = req.category or "其他"
    a.status = req.status or "其他"
    a.holder = req.holder or None
    a.application_date = req.application_date or None
    a.achieve_date = req.achieve_date or None
    a.remark = req.remark or None
    db.commit()
    db.refresh(a)
    _audit("admin", "PUT /achievements", f"编辑成果 #{achievement_id}：{a.name}")
    pmap = {p.id: p.name for p in db.query(Project).all()}
    return _achievement_to_dict(a, pmap.get(a.project_id, ""))


@app.get("/achievements/{achievement_id}/download", summary="下载成果文件")
def download_achievement_file(achievement_id: int, db: Session = Depends(get_db)):
    a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
    if not a or not a.file_path:
        raise HTTPException(status_code=404, detail="成果文件不存在")
    if not os.path.exists(a.file_path):
        raise HTTPException(status_code=404, detail="文件在磁盘上不存在")
    return FileResponse(path=a.file_path, filename=a.file_name or "成果文件", media_type="application/octet-stream")


def _sync_achievements_from_files(db: Session) -> int:
    """把文件管理里分类为「成果」的文件同步到成果台账（幂等：已同步的跳过）。
    返回本次新增的成果记录数。"""
    files = db.query(ProjectFile).filter(ProjectFile.category == "成果").all()
    if not files:
        return 0
    synced = {sid for (sid,) in db.query(Achievement.source_file_id).filter(Achievement.source_file_id.isnot(None)).all()}
    created = 0
    for f in files:
        if f.id in synced:
            continue
        a = Achievement(
            project_id=f.project_id,
            name=os.path.splitext(f.original_name)[0] or f.original_name,
            category=f.doc_type or "其他",
            status="已登记",
            holder=None,
            achieve_date=None,
            remark=f.ai_summary or "",
            file_name=f.original_name,
            file_path=f.file_path,
            processing_status="done",
            source="file",
            source_file_id=f.id,
        )
        db.add(a)
        created += 1
    if created:
        db.commit()
    return created


@app.get("/achievements", summary="成果列表（可按项目筛选，含文件管理同步的成果）")
def list_achievements(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    try:
        _sync_achievements_from_files(db)
    except Exception as e:
        logger.error(f"[成果] 从文件管理同步失败：{e}")
    q = db.query(Achievement)
    if project_id is not None:
        q = q.filter(Achievement.project_id == project_id)
    items = q.order_by(Achievement.created_at.desc()).all()
    pmap = {p.id: p.name for p in db.query(Project).all()}
    return [_achievement_to_dict(a, pmap.get(a.project_id, "")) for a in items]


@app.delete("/achievements/{achievement_id}", summary="删除成果")
def delete_achievement(achievement_id: int, db: Session = Depends(get_db)):
    a = db.query(Achievement).filter(Achievement.id == achievement_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="成果不存在")
    # 仅当该成果是「独立上传」的文件（非从文件管理同步）时才删除磁盘文件；
    # 从文件管理同步来的成果（source_file_id 非空）只删台账记录，不影响文件管理里的文件。
    if a.source_file_id is None and a.file_path and os.path.exists(a.file_path):
        try:
            os.remove(a.file_path)
        except Exception:
            pass
    db.delete(a)
    db.commit()
    return {"message": f"成果「{a.name}」已删除"}


class AchievementBatchDeleteRequest(BaseModel):
    ids: List[int]


@app.post("/achievements/batch-delete", summary="批量删除成果（框选一键删除）")
def batch_delete_achievements(req: AchievementBatchDeleteRequest, db: Session = Depends(get_db)):
    deleted = []
    not_found = []
    for aid in req.ids:
        a = db.query(Achievement).filter(Achievement.id == aid).first()
        if not a:
            not_found.append(aid)
            continue
        # 仅独立上传的成果（非文件管理同步）删除时才删磁盘文件，避免误删文件管理里的文件
        if a.source_file_id is None and a.file_path and os.path.exists(a.file_path):
            try:
                os.remove(a.file_path)
            except Exception:
                pass
        deleted.append(a.name or a.file_name or "")
        db.delete(a)
    db.commit()
    _audit("admin", "POST /achievements/batch-delete", f"批量删除 {len(deleted)} 条成果")
    return {"deleted": deleted, "not_found": not_found, "count": len(deleted)}


def _normalize_dup_key(filename: str) -> str:
    """文件名归一化，用于成果查重：去扩展名、去所有空白、去 (OCR/扫描/文本/副本) 等冗余标记、统一小写。"""
    if not filename:
        return ""
    s = filename.strip()
    s = os.path.splitext(s)[0]                      # 去扩展名
    s = re.sub(r"\s+", "", s)                        # 去所有空白
    # 去掉文件名里的 (OCR)/(扫描)/(文本)/(副本/复制) 标记（含全角括号），如 "xxx(OCR).pdf" → "xxx"
    s = re.sub(r"[（(][^（）()]*?(?:OCR|扫描|文本|副本|复制)[^（）()]*?[）)]", "", s, flags=re.IGNORECASE)
    return s.lower()


def _normalize_name_key(name: str) -> str:
    """成果名称归一化，用于同名查重：去首尾/内部空白、统一小写。"""
    if not name:
        return ""
    s = str(name).strip()
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _achievement_dup_info(a) -> tuple:
    """返回成果查重的 (group_key, display_name)。

    判重优先级：先按「成果名称」归一化查重，名称缺失时回退「文件名」归一化。
    group_key 三元组为 (base, project_id_or_0, source_file_id_or_0)，用于把「同名」
    记录进一步按项目与来源文件区分，避免把以下「同名但非重复」的记录误判为重复：
      - 不同项目下的同名成果（如各项目各自的「科技查新报告」）；
      - 同一项目内从不同文件管理文件同步而来的同名记录（source_file_id 不同）。
    base 为空表示不参与查重（既无名称也无文件名）。
    """
    display = a.name or a.file_name or ""
    base = _normalize_name_key(a.name)
    if not base:
        base = _normalize_dup_key(a.file_name)
    if not base:
        return (None, display)
    pid = a.project_id if a.project_id is not None else 0
    sfid = a.source_file_id if a.source_file_id is not None else 0
    return ((base, pid, sfid), display)


def _group_achievement_duplicates(items) -> list:
    """按查重键把成果分组，返回重复组列表 [(group_key, display_name, [Achievement, ...])]，仅含 >=2 条的组。"""
    groups: dict = {}
    names: dict = {}
    for a in items:
        gkey, disp = _achievement_dup_info(a)
        if gkey is None:
            continue
        groups.setdefault(gkey, []).append(a)
        names.setdefault(gkey, disp)
    out = []
    for gkey, lst in groups.items():
        if len(lst) < 2:
            continue
        out.append((gkey, names.get(gkey, gkey[0]), lst))
    return out


@app.get("/achievements/duplicates", summary="查重：扫描成果台账中的重复记录（只读，不删除）")
def achievement_duplicates(db: Session = Depends(get_db)):
    """按「成果名称（优先）/文件名归一化 + 项目 + 来源文件」把成果分组，同一 key 出现多次即为重复；每组最新一条为保留项。"""
    items = db.query(Achievement).all()
    pmap = {p.id: p.name for p in db.query(Project).all()}

    result = []
    for gkey, disp, lst in _group_achievement_duplicates(items):
        lst_sorted = sorted(lst, key=lambda x: (x.created_at or datetime.min, x.id))
        keep = lst_sorted[-1]
        remove = lst_sorted[:-1]
        result.append({
            "key": disp,
            "count": len(lst),
            "keep": _achievement_to_dict(keep, pmap.get(keep.project_id, "")),
            "remove": [_achievement_to_dict(x, pmap.get(x.project_id, "")) for x in remove],
            "remove_ids": [x.id for x in remove],
        })
    return {
        "groups": result,
        "group_count": len(result),
        "total_remove": sum(len(g["remove"]) for g in result),
    }


@app.post("/achievements/deduplicate", summary="去重：每组重复成果只保留最新一条，其余删除")
def achievement_deduplicate(db: Session = Depends(get_db)):
    items = db.query(Achievement).all()

    deleted_ids = []
    deleted_names = []
    for gkey, disp, lst in _group_achievement_duplicates(items):
        lst_sorted = sorted(lst, key=lambda x: (x.created_at or datetime.min, x.id))
        for x in lst_sorted[:-1]:
            # 仅独立上传的成果（非文件管理同步）删除时才删磁盘文件
            if x.source_file_id is None and x.file_path and os.path.exists(x.file_path):
                try:
                    os.remove(x.file_path)
                except Exception:
                    pass
            deleted_ids.append(x.id)
            deleted_names.append(x.name or x.file_name or "")
            db.delete(x)
    db.commit()
    _audit("admin", "POST /achievements/deduplicate", f"成果查重去重，删除 {len(deleted_ids)} 条重复记录")
    return {"deleted_ids": deleted_ids, "deleted_names": deleted_names, "count": len(deleted_ids)}


@app.get("/achievements/stats", summary="成果统计（按类型/状态/项目/年份）")
def achievement_stats(db: Session = Depends(get_db)):
    try:
        _sync_achievements_from_files(db)
    except Exception as e:
        logger.error(f"[成果] 从文件管理同步失败：{e}")
    items = db.query(Achievement).all()
    pmap = {p.id: p.name for p in db.query(Project).all()}
    by_category = {}
    by_status = {}
    by_project = {}
    by_year = {}
    for a in items:
        c = a.category or "其他"
        by_category[c] = by_category.get(c, 0) + 1
        s = a.status or "其他"
        by_status[s] = by_status.get(s, 0) + 1
        pname = pmap.get(a.project_id, "平台级")
        by_project[pname] = by_project.get(pname, 0) + 1
        y = (a.achieve_date or "")[:4] or "未标注"
        by_year[y] = by_year.get(y, 0) + 1
    return {
        "total_count": len(items),
        "by_category": by_category,
        "by_status": by_status,
        "by_project": by_project,
        "by_year": by_year,
        "categories": ACHIEVEMENT_CATEGORIES,
        "statuses": ACHIEVEMENT_STATUSES,
    }


# ============ 提醒中心 ============
@app.get("/reminders", summary="提醒中心：聚合合同超期/临期、经费预警等")
def reminders(db: Session = Depends(get_db)):
    overdue = []
    upcoming = []
    for c in db.query(Contract).all():
        status, days = _contract_status(c.due_date)
        if status == "已超期":
            overdue.append((c, days))
        elif status == "即将到期":
            upcoming.append((c, days))

    items = []
    for c, days in overdue:
        items.append({
            "type": "合同超期",
            "level": "danger",
            "title": c.contract_name or c.original_name,
            "detail": f"已超期 {abs(days)} 天，到期日 {c.due_date or '未知'}",
            "link": "合同管理",
        })
    for c, days in upcoming:
        items.append({
            "type": "合同临期",
            "level": "warning",
            "title": c.contract_name or c.original_name,
            "detail": f"还有 {days} 天到期，到期日 {c.due_date or '未知'}",
            "link": "合同管理",
        })

    # 经费预警：支出 > 到账
    fundings = db.query(Funding).all()
    income = sum(f.amount or 0 for f in fundings if f.fund_type == "到账")
    expense = sum(f.amount or 0 for f in fundings if f.fund_type == "支出")
    if expense > income and (income + expense) > 0:
        items.append({
            "type": "经费预警",
            "level": "warning",
            "title": "经费支出超出到账",
            "detail": f"到账 {income:,.2f} 元，支出 {expense:,.2f} 元，超支 {expense - income:,.2f} 元",
            "link": "经费管理",
        })

    level_order = {"danger": 0, "warning": 1, "info": 2}
    items.sort(key=lambda r: level_order.get(r["level"], 9))
    return {
        "total": len(items),
        "overdue_count": len(overdue),
        "upcoming_count": len(upcoming),
        "items": items,
    }


# ============ 数据大屏 ============
@app.get("/dashboard", summary="数据大屏：宏观指标聚合")
def dashboard(db: Session = Depends(get_db)):
    from sqlalchemy import func

    # 计数：数据库 COUNT 聚合，避免加载全表
    project_count = db.query(func.count(Project.id)).scalar() or 0
    file_count = db.query(func.count(ProjectFile.id)).scalar() or 0
    contract_count = db.query(func.count(Contract.id)).scalar() or 0
    achievement_count = db.query(func.count(Achievement.id)).scalar() or 0
    funding_count = db.query(func.count(Funding.id)).scalar() or 0

    # 文件分类 / 阶段分布：GROUP BY 聚合
    file_by_category = {
        (k or "其他"): v
        for k, v in db.query(ProjectFile.category, func.count(ProjectFile.id))
        .group_by(ProjectFile.category).all()
    }
    file_by_stage = {
        (k or "未划分"): v
        for k, v in db.query(ProjectFile.stage, func.count(ProjectFile.id))
        .group_by(ProjectFile.stage).all()
    }

    # 各项目文件数：GROUP BY project_id，再映射项目名（仅读 id/name 两列）
    pmap = dict(db.query(Project.id, Project.name).all())
    project_file_count = {}
    for pid, cnt in db.query(ProjectFile.project_id, func.count(ProjectFile.id)) \
            .group_by(ProjectFile.project_id).all():
        project_file_count[pmap.get(pid, "未知")] = cnt

    # 合同总额：SUM 聚合；状态依赖业务逻辑（_contract_status），只读 due_date 遍历
    contract_total_amount = round(db.query(func.sum(Contract.amount_value)).scalar() or 0, 2)
    contract_by_status = {}
    overdue_count = 0
    for (due_date,) in db.query(Contract.due_date).all():
        status, _ = _contract_status(due_date)
        contract_by_status[status] = contract_by_status.get(status, 0) + 1
        if status == "已超期":
            overdue_count += 1

    # 成果分类分布：GROUP BY 聚合
    achievement_by_category = {
        (k or "其他"): v
        for k, v in db.query(Achievement.category, func.count(Achievement.id))
        .group_by(Achievement.category).all()
    }

    # 经费：按类型 SUM 聚合
    fund_by_type = dict(db.query(Funding.fund_type, func.sum(Funding.amount))
                        .group_by(Funding.fund_type).all())
    budget = round(fund_by_type.get("预算") or 0, 2)
    income = round(fund_by_type.get("到账") or 0, 2)
    expense = round(fund_by_type.get("支出") or 0, 2)

    return {
        "project_count": project_count,
        "file_count": file_count,
        "contract_count": contract_count,
        "achievement_count": achievement_count,
        "funding_count": funding_count,
        "contract_total_amount": contract_total_amount,
        "contract_by_status": contract_by_status,
        "overdue_count": overdue_count,
        "file_by_category": file_by_category,
        "file_by_stage": file_by_stage,
        "achievement_by_category": achievement_by_category,
        "project_file_count": project_file_count,
        "funding": {
            "budget": round(budget, 2),
            "income": round(income, 2),
            "expense": round(expense, 2),
            "balance": round(income - expense, 2),
        },
    }

