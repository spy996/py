"""
研发知识智能管理平台 - Streamlit 网页界面
功能：项目管理 + 文件管理 + 文件内容预览
"""


import os
import json
import time
import streamlit as st
import requests
import html
import re

from constants import PROJECT_STAGES
from ui_common import fmt_money, time_filter_widget, stage_progress


# ★★★ 让 requests 绕过系统代理，直接访问本机后端 ★★★
# （否则会走 127.0.0.1:12334 代理，导致 ProxyError）
_session = requests.Session()
_session.trust_env = False          # 关键：忽略系统代理和环境变量里的代理设置
requests.get = _session.get
requests.post = _session.post
requests.put = _session.put
requests.delete = _session.delete

# ============ 基础配置 ============
API_BASE = "http://127.0.0.1:8000"
CHAT_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_history.json")

# 问答范围选项（label → 后端 source_type，镜像 main.py 的 QA_SOURCES，不跨端 import）
QA_SOURCE_OPTIONS = {
    "📚 资料": "file",
    "📄 合同": "contract",
    "💰 经费": "funding",
    "🏆 成果": "achievement",
    "✅ 待办": "todo",
    "📁 项目信息": "project",
}

# 引用来源图标（source_type → emoji）
ICON = {
    "file": "📄",
    "document_summary": "📄",
    "contract": "📄",
    "funding": "💰",
    "achievement": "🏆",
    "todo": "✅",
    "project": "📁",
}

st.set_page_config(
    page_title="研发知识智能管理平台",
    page_icon="📚",
    layout="wide",
)

# ============ 全局样式美化 ============
st.markdown(
    """
    <style>
        :root {
            --brand: #1f5fa8;
            --brand-dark: #16324f;
            --accent: #2563eb;
            --accent-strong: #1d4ed8;
            --border: #e5eaf0;
            --border-soft: #eef2f6;
            --muted: #667085;
        }
        /* 全局背景：柔和浅灰蓝，降低大面积纯白带来的刺眼感 */
        .stApp {
            background-color: #f4f6f9;
        }
        [data-testid="stHeader"] {
            background: transparent;
        }
        /* 主内容区：更舒展的留白与最大宽度 */
        .block-container {
            padding-top: 1.8rem;
            padding-bottom: 3rem;
            max-width: 1440px;
        }
        /* 标题层级 */
        h1 { color: var(--brand-dark); font-weight: 750; letter-spacing: -.2px; }
        h2 { color: var(--brand-dark); font-weight: 650; }
        h3 { color: var(--brand); font-weight: 600; }
        /* 卡片（st.container border）圆角 + 柔和阴影 + hover */
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            border-radius: 14px;
            border: 1px solid var(--border);
            box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 4px 14px rgba(16, 24, 40, 0.05);
            padding: 0.85rem 1.05rem;
            background: #ffffff;
            transition: border-color .18s ease, box-shadow .18s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div:hover {
            border-color: #cfd9e4;
            box-shadow: 0 2px 4px rgba(16, 24, 40, 0.06), 0 10px 24px rgba(16, 24, 40, 0.08);
        }
        /* 指标卡片 */
        [data-testid="stMetric"] {
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 0.8rem 0.95rem;
            background: linear-gradient(180deg, #ffffff 0%, #fafcfe 100%);
            box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
        }
        [data-testid="stMetric"] label { color: var(--muted); font-size: 0.84rem; }
        [data-testid="stMetricValue"] { color: var(--brand-dark); font-weight: 700; }
        /* 折叠区更精致 */
        div[data-testid="stExpander"] details {
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 2px 12px;
            background: #fbfcfe;
        }
        div[data-testid="stExpander"] summary { font-weight: 550; color: #344054; }
        /* 按钮：圆角 + 边框 + hover 反馈 */
        .stButton button, .stDownloadButton button, .stFormSubmitButton button {
            border-radius: 9px;
            border: 1px solid var(--border);
            background: #ffffff;
            color: #344054;
            font-weight: 520;
            transition: all .15s ease;
        }
        .stButton button:hover, .stDownloadButton button:hover, .stFormSubmitButton button:hover {
            border-color: var(--accent);
            color: var(--accent);
            background: #f4f8ff;
        }
        .stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] {
            background: var(--accent);
            border-color: var(--accent);
            color: #ffffff;
        }
        .stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primary"]:hover {
            background: var(--accent-strong);
            border-color: var(--accent-strong);
            color: #ffffff;
        }
        /* 侧边栏：浅色渐变背景 + 分隔（比主背景略深，形成层次） */
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #edf0f4 0%, #e7ebf0 100%);
            border-right: 1px solid var(--border);
        }
        .sidebar-brand {
            font-size: 1.3rem;
            font-weight: 750;
            color: var(--brand-dark);
            padding: 0.35rem 0 0.1rem;
            line-height: 1.35;
        }
        /* 导航项：加大字号 + 更宽松的点击区域 */
        section[data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 8px;
            padding: 5px 6px;
            font-size: 1.08rem;
            transition: background .12s ease;
        }
        section[data-testid="stSidebar"] [role="radiogroup"] label p {
            font-size: 1.08rem;
            font-weight: 500;
        }
        section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: #dbe4ee;
        }
        /* 输入控件圆角 */
        [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
        [data-testid="stNumberInput"] input {
            border-radius: 8px;
        }
        /* 表格圆角 */
        [data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid var(--border);
        }
        /* 分隔线 */
        hr { border-color: var(--border-soft); }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============ 登录认证 ============
def api_login(username, password):
    """调用后端登录接口，返回 token。"""
    try:
        return requests.post(
            f"{API_BASE}/auth/login",
            json={"username": username, "password": password},
            timeout=15,
        )
    except Exception as e:
        return None


def _apply_auth():
    """把登录 token 注入到全局 session 的请求头，让所有 API 请求自动带上鉴权。"""
    token = st.session_state.get("token", "")
    if token:
        _session.headers["Authorization"] = f"Bearer {token}"
    else:
        _session.headers.pop("Authorization", None)


def _maybe_refresh_token():
    """token 到期前 24 小时内自动续期（调用 /auth/refresh），失败则静默（等下次或 401 时重新登录）。"""
    expires_at = st.session_state.get("expires_at", 0)
    if not expires_at:
        return
    remaining = expires_at - int(time.time())
    if remaining > 24 * 3600:
        return
    try:
        resp = requests.post(f"{API_BASE}/auth/refresh", timeout=15)
    except Exception:
        return
    if resp.status_code == 200:
        data = resp.json()
        st.session_state["token"] = data["token"]
        st.session_state["expires_at"] = data["expires_at"]
        st.session_state["username"] = data["username"]
        _apply_auth()


def api_change_password(username, old_password, new_password):
    """调用后端改密接口，返回 resp；改密成功后旧 token 失效，需重新登录。"""
    try:
        return requests.post(
            f"{API_BASE}/auth/change-password",
            json={"username": username, "old_password": old_password, "new_password": new_password},
            timeout=15,
        )
    except Exception as e:
        return None


def api_audit_archive(days=None):
    try:
        params = {"days": days} if days else {}
        return requests.post(f"{API_BASE}/audit/archive", params=params, timeout=120)
    except Exception as e:
        st.error(f"归档请求失败：{e}")
        return None


# ============ 审计日志 / 备份 API ============
def api_audit_logs(limit=300):
    try:
        resp = requests.get(f"{API_BASE}/audit/logs", params={"limit": limit}, timeout=30)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def api_backup_create():
    try:
        return requests.post(f"{API_BASE}/backup/create", timeout=600)
    except Exception as e:
        st.error(f"备份请求失败：{e}")
        return None


def api_backup_list():
    try:
        resp = requests.get(f"{API_BASE}/backup/list", timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


# ============ 经费管理 API ============
def api_list_fundings(project_id=None):
    try:
        params = {"project_id": project_id} if project_id is not None else {}
        resp = requests.get(f"{API_BASE}/fundings", params=params, timeout=30)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def api_create_funding(payload):
    try:
        return requests.post(f"{API_BASE}/fundings", json=payload, timeout=30)
    except Exception as e:
        st.error(f"经费录入失败：{e}")
        return None


def api_delete_funding(fid):
    try:
        return requests.delete(f"{API_BASE}/fundings/{fid}", timeout=30)
    except Exception as e:
        st.error(f"删除失败：{e}")
        return None


def api_funding_stats(start=None, end=None):
    try:
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        resp = requests.get(f"{API_BASE}/fundings/stats", params=params, timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


# ============ 年度经费一览表 API ============
def api_funding_grid(year):
    try:
        resp = requests.get(f"{API_BASE}/funding-grid", params={"year": year}, timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"获取年度经费表失败：{e}")
        return None


def api_funding_grid_summary():
    try:
        resp = requests.get(f"{API_BASE}/funding-grid/summary", timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def api_add_funding_row(year, project_id):
    try:
        return requests.post(f"{API_BASE}/funding-grid/rows", json={"year": year, "project_id": project_id}, timeout=30)
    except Exception as e:
        st.error(f"添加项目行失败：{e}")
        return None


def api_delete_funding_row(row_id):
    try:
        return requests.delete(f"{API_BASE}/funding-grid/rows/{row_id}", timeout=30)
    except Exception as e:
        st.error(f"删除项目行失败：{e}")
        return None


def api_move_funding_row(row_id, direction):
    try:
        return requests.post(f"{API_BASE}/funding-grid/rows/{row_id}/move", json={"direction": direction}, timeout=30)
    except Exception as e:
        st.error(f"调整行顺序失败：{e}")
        return None


def api_save_funding_cell(payload):
    try:
        return requests.post(f"{API_BASE}/funding-grid/cells", json=payload, timeout=30)
    except Exception as e:
        st.error(f"保存单元格失败：{e}")
        return None


def api_upload_funding_attachments(year, project_id, month, files):
    try:
        data = {"year": str(year), "project_id": str(project_id), "month": str(month)}
        _files = [("files", (f.name, f.getvalue())) for f in files]
        return requests.post(f"{API_BASE}/funding-grid/attachments", data=data, files=_files, timeout=120)
    except Exception as e:
        st.error(f"上传附件失败：{e}")
        return None


def api_delete_funding_attachment(att_id):
    try:
        return requests.delete(f"{API_BASE}/funding-grid/attachments/{att_id}", timeout=30)
    except Exception as e:
        st.error(f"删除附件失败：{e}")
        return None


# ============ 成果台账 API ============
def api_list_achievements(project_id=None):
    try:
        params = {"project_id": project_id} if project_id is not None else {}
        resp = requests.get(f"{API_BASE}/achievements", params=params, timeout=30)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def api_create_achievement(payload):
    try:
        return requests.post(f"{API_BASE}/achievements", json=payload, timeout=30)
    except Exception as e:
        st.error(f"成果录入失败：{e}")
        return None


def api_create_achievement_manual(payload, file=None):
    """手动登记成果，可附带一个附件文件并与填写信息绑定（不做自动识别）。"""
    try:
        data = {
            "name": payload.get("name", ""),
            "category": payload.get("category", "其他"),
            "status": payload.get("status", "其他"),
            "holder": payload.get("holder") or "",
            "achieve_date": payload.get("achieve_date") or "",
            "remark": payload.get("remark") or "",
        }
        if payload.get("project_id") is not None:
            data["project_id"] = str(payload["project_id"])
        files = {"file": (file.name, file.getvalue())} if file is not None else None
        return requests.post(f"{API_BASE}/achievements/manual", data=data, files=files, timeout=120)
    except Exception as e:
        st.error(f"成果登记失败：{e}")
        return None


def api_delete_achievement(aid):
    try:
        return requests.delete(f"{API_BASE}/achievements/{aid}", timeout=30)
    except Exception as e:
        st.error(f"删除失败：{e}")
        return None


def api_achievement_stats():
    try:
        resp = requests.get(f"{API_BASE}/achievements/stats", timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def api_upload_achievements(uploaded_files, project_id):
    """批量上传成果文件，后端后台自动识别字段。uploaded_files 为 file_uploader(accept_multiple_files=True) 返回的列表。"""
    try:
        files = [("files", (f.name, f.getvalue())) for f in uploaded_files]
        data = {"project_id": str(project_id) if project_id is not None else ""}
        return requests.post(f"{API_BASE}/achievements/upload", files=files, data=data, timeout=300)
    except Exception as e:
        st.error(f"成果文件上传失败：{e}")
        return None


def api_update_achievement(aid, payload):
    try:
        return requests.put(f"{API_BASE}/achievements/{aid}", json=payload, timeout=30)
    except Exception as e:
        st.error(f"成果保存失败：{e}")
        return None


def api_achievement_duplicates():
    """查重扫描（只读），返回重复分组。"""
    try:
        resp = requests.get(f"{API_BASE}/achievements/duplicates", timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"查重扫描失败：{e}")
        return None


def api_achievement_deduplicate():
    """执行去重：每组只保留最新一条。"""
    try:
        resp = requests.post(f"{API_BASE}/achievements/deduplicate", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"去重失败：{e}")
        return None


def api_batch_delete_achievements(ids):
    """批量删除成果（框选一键删除）。"""
    try:
        return requests.post(f"{API_BASE}/achievements/batch-delete", json={"ids": ids}, timeout=60)
    except Exception as e:
        st.error(f"批量删除请求失败：{e}")
        return None


# ============ 提醒中心 / 数据大屏 API ============
def api_reminders():
    try:
        resp = requests.get(f"{API_BASE}/reminders", timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def api_dashboard():
    try:
        resp = requests.get(f"{API_BASE}/dashboard", timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


# ============ 辅助函数 ============
def api_create_project(code, name, description, stage="项目立项"):
    resp = requests.post(
        f"{API_BASE}/projects",
        json={"code": code, "name": name, "description": description, "stage": stage},
    )
    return resp


def api_update_project(project_id, payload):
    try:
        return requests.put(f"{API_BASE}/projects/{project_id}", json=payload, timeout=30)
    except Exception as e:
        st.error(f"项目更新失败：{e}")
        return None


def api_list_projects():
    resp = requests.get(f"{API_BASE}/projects")
    if resp.status_code == 200:
        return resp.json()
    return []


def api_delete_project(project_id):
    return requests.delete(f"{API_BASE}/projects/{project_id}")


def api_list_files(project_id):
    resp = requests.get(f"{API_BASE}/projects/{project_id}/files")
    if resp.status_code == 200:
        return resp.json()
    return []


def api_upload_file(project_id, file):
    files = {"file": (file.name, file.getvalue())}
    return requests.post(f"{API_BASE}/projects/{project_id}/files", files=files)


def api_download_file(file_id):
    return requests.get(f"{API_BASE}/files/{file_id}/download")

def api_delete_file(file_id):
    return requests.delete(f"{API_BASE}/files/{file_id}")

def api_download_zip(project_id, file_ids):
    """调用后端批量下载接口，把 file_ids 列表打包成 zip 返回"""
    try:
        resp = requests.post(
            f"{API_BASE}/projects/{project_id}/download-zip",
            json={"file_ids": file_ids},
            timeout=600,
        )
        return resp
    except Exception as e:
        st.error(f"批量下载请求失败：{e}")
        return None


def _prepare_zip_download(project_id, file_ids, zip_name):
    """请求后端打包 zip 并存入 session_state，rerun 后渲染下载按钮（避免每次刷新都重新打包）。"""
    resp = api_download_zip(project_id, file_ids)
    if resp is None:
        return
    if resp.status_code != 200:
        st.error(f"打包失败：{resp.text}")
        return
    st.session_state["zip_bytes"] = resp.content
    st.session_state["zip_name"] = zip_name
    st.rerun()


# ============ 合同管理 API ============
def api_upload_contracts(uploaded_files, project_id=None):
    """批量上传合同文件，后端自动抽取字段。uploaded_files 为 file_uploader 返回的列表。"""
    try:
        files = [("files", (f.name, f.getvalue())) for f in uploaded_files]
        data = {"project_id": project_id} if project_id else {}
        return requests.post(f"{API_BASE}/contracts", files=files, data=data, timeout=600)
    except Exception as e:
        st.error(f"合同上传请求失败：{e}")
        return None


def api_list_contracts(project_id=None):
    try:
        params = {"project_id": project_id} if project_id is not None else {}
        resp = requests.get(f"{API_BASE}/contracts", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


# LLM 抽取的合同名可能过于泛化（如「物资设备采购合同」），无法区分具体标的；
# 此类泛化名回退到原始文件名（去扩展名），并在下拉里附上项目名/编号，便于识别。
_GENERIC_CONTRACT_NAMES = {
    "合同", "协议", "采购合同", "物资设备采购合同", "设备采购合同", "技术服务合同",
    "服务合同", "合作协议", "业务协议", "服务协议", "采购协议", "销售合同",
    "买卖合同", "采购合同书", "合同书", "协议书",
}


def _contract_display_name(c):
    """返回合同的可读名称：泛化名（如「物资设备采购合同」）回退到原始文件名，不含 #id/项目前缀。"""
    name = (c.get("contract_name") or "").strip()
    orig = (c.get("original_name") or "").strip()
    if not name or name in _GENERIC_CONTRACT_NAMES:
        base = os.path.splitext(orig)[0].strip() if orig else ""
        name = base or name
    return name or "(未命名)"


def _contract_display_label(c, with_project=True):
    """生成合同的友好显示名：项目名 + #id + 名称 + 编号；名称太泛化时回退原始文件名。"""
    name = _contract_display_name(c)
    no = (c.get("contract_no") or "").strip()
    label = f"#{c['id']}｜{name}"
    if no:
        label += f"｜{no}"
    if with_project:
        pname = (c.get("project_name") or "").strip()
        if pname:
            label = f"{pname} ｜ {label}"
    return label


def api_contract_stats(start=None, end=None, date_field="sign_date", project_id=None):
    try:
        params = {"date_field": date_field}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if project_id is not None:
            params["project_id"] = project_id
        resp = requests.get(f"{API_BASE}/contracts/stats", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"合同统计请求失败：{e}")
        return None


def api_download_contract(cid):
    return requests.get(f"{API_BASE}/contracts/{cid}/download")


def api_contract_content(cid):
    return requests.get(f"{API_BASE}/contracts/{cid}/content")


def api_delete_contract(cid):
    return requests.delete(f"{API_BASE}/contracts/{cid}")


def api_batch_delete_contracts(ids):
    try:
        return requests.post(f"{API_BASE}/contracts/batch-delete", json={"ids": ids}, timeout=60)
    except Exception as e:
        st.error(f"批量删除请求失败：{e}")
        return None


def api_contract_duplicates():
    """合同查重扫描（只读），返回重复分组。"""
    try:
        resp = requests.get(f"{API_BASE}/contracts/duplicates", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"合同查重扫描失败：{e}")
        return None


def api_contract_deduplicate():
    """执行合同去重：每组只保留最新一条。"""
    try:
        resp = requests.post(f"{API_BASE}/contracts/deduplicate", timeout=120)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"合同去重失败：{e}")
        return None


# ============ 从文件管理导入合同 ============
def api_contract_import_candidates():
    try:
        resp = requests.get(f"{API_BASE}/contracts/import-candidates", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"扫描合同候选失败：{e}")
        return None


def api_contract_import(file_ids):
    try:
        return requests.post(f"{API_BASE}/contracts/import", json={"file_ids": file_ids}, timeout=600)
    except Exception as e:
        st.error(f"导入合同失败：{e}")
        return None


# ============ 合同：人工确认 + 执行计划节点 + 变更追溯 ============
def api_update_contract(cid, payload):
    try:
        return requests.put(f"{API_BASE}/contracts/{cid}", json=payload, timeout=60)
    except Exception as e:
        st.error(f"合同更新失败：{e}")
        return None


def api_confirm_contract(cid, project_id=None):
    try:
        payload = {"project_id": project_id} if project_id else None
        return requests.post(f"{API_BASE}/contracts/{cid}/confirm", json=payload, timeout=60)
    except Exception as e:
        st.error(f"合同确认失败：{e}")
        return None


def api_ignore_contract_alert(cid):
    try:
        return requests.post(f"{API_BASE}/contracts/{cid}/ignore-alert", timeout=30)
    except Exception as e:
        st.error(f"忽略预警失败：{e}")
        return None


def api_unignore_contract_alert(cid):
    try:
        return requests.post(f"{API_BASE}/contracts/{cid}/unignore-alert", timeout=30)
    except Exception as e:
        st.error(f"恢复预警失败：{e}")
        return None


def api_contract_nodes(cid):
    try:
        resp = requests.get(f"{API_BASE}/contracts/{cid}/nodes", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def api_update_contract_node(node_id, payload):
    try:
        return requests.post(f"{API_BASE}/contract-nodes/{node_id}/update", json=payload, timeout=60)
    except Exception as e:
        st.error(f"节点更新失败：{e}")
        return None


def api_update_contract_node_status(node_id, payload):
    try:
        return requests.post(f"{API_BASE}/contract-nodes/{node_id}/status", json=payload, timeout=60)
    except Exception as e:
        st.error(f"节点状态更新失败：{e}")
        return None


# ============ 财务资料（暂估单/结算单/发票） ============
def api_list_financial_docs(contract_id=None, project_id=None):
    try:
        params = {}
        if contract_id is not None:
            params["contract_id"] = contract_id
        if project_id is not None:
            params["project_id"] = project_id
        resp = requests.get(f"{API_BASE}/financial-docs", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def api_upload_financial_docs(contract_id, doc_type, project_id, files):
    try:
        fs = [("files", (f.name, f.getvalue())) for f in files]
        data = {"contract_id": contract_id, "doc_type": doc_type}
        if project_id:
            data["project_id"] = project_id
        return requests.post(f"{API_BASE}/financial-docs", files=fs, data=data, timeout=600)
    except Exception as e:
        st.error(f"财务资料上传失败：{e}")
        return None


def api_update_financial_doc(did, payload):
    try:
        return requests.put(f"{API_BASE}/financial-docs/{did}", json=payload, timeout=60)
    except Exception as e:
        st.error(f"财务资料更新失败：{e}")
        return None


def api_confirm_financial_doc(did):
    try:
        return requests.post(f"{API_BASE}/financial-docs/{did}/confirm", timeout=60)
    except Exception as e:
        st.error(f"财务资料确认失败：{e}")
        return None


def api_delete_financial_doc(did):
    try:
        return requests.delete(f"{API_BASE}/financial-docs/{did}", timeout=60)
    except Exception as e:
        st.error(f"财务资料删除失败：{e}")
        return None


def api_financial_files(project_id=None, doc_type=None):
    """经费管理引用：文件库中自动归集的财务单据（发票/结算单/暂估单/估算单等）。"""
    try:
        params = {}
        if project_id:
            params["project_id"] = project_id
        if doc_type:
            params["doc_type"] = doc_type
        resp = requests.get(f"{API_BASE}/funding/financial-files", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"获取财务单据失败：{e}")
        return None


def api_correct_classifications():
    try:
        return requests.post(f"{API_BASE}/admin/correct-classifications", timeout=120)
    except Exception as e:
        st.error(f"分类修正失败：{e}")
        return None


# ============ 系统配置 ============
def api_get_config():
    try:
        resp = requests.get(f"{API_BASE}/config", timeout=30)
        return resp.json() if resp.status_code == 200 else {}
    except Exception:
        return {}


def api_update_config(config_dict):
    try:
        return requests.put(f"{API_BASE}/config", json={"config": config_dict}, timeout=30)
    except Exception as e:
        st.error(f"配置更新失败：{e}")
        return None


# ============ 经费比对 / 预警 / 预算 ============
def api_funding_compare(project_id=None):
    try:
        params = {"project_id": project_id} if project_id else {}
        resp = requests.get(f"{API_BASE}/funding/compare", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"经费比对失败：{e}")
        return None


def api_funding_alerts():
    try:
        resp = requests.get(f"{API_BASE}/funding/alerts", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def api_funding_budget(start=None, end=None):
    try:
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        resp = requests.get(f"{API_BASE}/funding/budget", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def api_financial_docs_year_summary():
    try:
        resp = requests.get(f"{API_BASE}/financial-docs/year-summary", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


# ============ 项目管理聚合 / 合同金额覆盖 / 预警关闭 ============
def api_project_dashboard(project_id):
    try:
        resp = requests.get(f"{API_BASE}/projects/{project_id}/dashboard", timeout=60)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        st.error(f"获取项目数据失败：{e}")
        return None


def api_save_contract_amount_override(payload):
    try:
        return requests.post(f"{API_BASE}/funding-grid/contract-amount-override", json=payload, timeout=30)
    except Exception as e:
        st.error(f"修改金额失败：{e}")
        return None


def api_dismiss_alert(alert_key, category):
    try:
        return requests.post(
            f"{API_BASE}/funding/alerts/dismiss",
            json={"alert_key": alert_key, "category": category},
            timeout=30,
        )
    except Exception as e:
        st.error(f"关闭提醒失败：{e}")
        return None


def api_dismiss_expired_alert():
    try:
        return requests.post(f"{API_BASE}/funding/alerts/dismiss-expired", timeout=60)
    except Exception as e:
        st.error(f"批量清理失败：{e}")
        return None


# ============ 待办模块 ============
def api_list_todos(status=None, category=None):
    try:
        params = {}
        if status:
            params["status"] = status
        if category:
            params["category"] = category
        resp = requests.get(f"{API_BASE}/todos", params=params, timeout=60)
        return resp.json() if resp.status_code == 200 else {"todos": [], "unhandled": 0}
    except Exception:
        return {"todos": [], "unhandled": 0}


def api_todo_count():
    try:
        resp = requests.get(f"{API_BASE}/todos/count", timeout=30)
        return resp.json().get("unhandled", 0) if resp.status_code == 200 else 0
    except Exception:
        return 0


def api_handle_todo(tid):
    try:
        return requests.post(f"{API_BASE}/todos/{tid}/handle", timeout=30)
    except Exception:
        return None


def api_handle_all_todos():
    try:
        return requests.post(f"{API_BASE}/todos/handle-all", timeout=60)
    except Exception:
        return None


def api_refresh_todos():
    try:
        return requests.post(f"{API_BASE}/todos/refresh", timeout=60)
    except Exception:
        return None


def _make_summary(text, max_len=80):
    """把文件内容取开头一段，压成 50~100 字左右的简述"""
    if not text or not str(text).strip():
        return "（暂无内容）"
    # 把换行和多余空格压成一个空格，合并成一段
    clean = " ".join(str(text).split())
    if len(clean) <= max_len:
        return clean
    return clean[:max_len].rstrip("，。；、,. :") + "……"


def _file_type(name):
    """根据文件名后缀返回文件类型中文名"""
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    mapping = {
        "pdf": "PDF 文档",
        "doc": "Word 文档",
        "docx": "Word 文档",
        "xls": "Excel 表格",
        "xlsx": "Excel 表格",
        "xlsm": "Excel 表格",
        "png": "图片",
        "jpg": "图片",
        "jpeg": "图片",
        "bmp": "图片",
        "tif": "图片",
        "tiff": "图片",
        "webp": "图片",
        "txt": "文本文件",
        "md": "文本文件",
        "csv": "文本文件",
        "log": "文本文件",
        "json": "文本文件",
        "py": "文本文件",
        "html": "文本文件",
        "xml": "文本文件",
    }
    return mapping.get(ext, ext.upper() if ext else "未知")

def _format_date(ts):
    """把后端返回的 ISO 时间串格式化成「2026-08-22 12:34」的样子"""
    if not ts:
        return ""
    s = str(ts).replace("T", " ").strip()
    return s[:19]

def api_get_content(file_id):
    return requests.get(f"{API_BASE}/files/{file_id}/content")

def api_search(keyword):
    return _session.get(f"{API_BASE}/search", params={"q": keyword}, timeout=60)

def _load_chat_history():
    """从本地文件读取聊天记录"""
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_chat_history(history):
    """把聊天记录保存到本地文件"""
    try:
        with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"保存聊天记录失败：{e}")

@st.cache_data(show_spinner=False)
def api_summarize(file_id):
    """让后端大模型为指定文件生成一句话总结（结果会缓存，避免重复调用）"""
    try:
        resp = requests.post(f"{API_BASE}/files/{file_id}/summary", timeout=120)
        if resp.status_code == 200:
            return resp.json().get("summary", "（总结为空）")
        return f"（总结失败：{resp.status_code}）"
    except Exception as e:
        return f"（总结失败：{e}）"

def api_project_stats(project_id, question):
    """调用后端合同统计接口（直接查摘要库，精确统计）"""
    try:
        resp = requests.post(
            f"{API_BASE}/projects/{project_id}/stats",
            json={"question": question},
            timeout=600,
        )
        return resp
    except Exception as e:
        st.error(f"合同统计请求失败：{e}")
        return None

def api_documents_by_stage(project_id):
    """获取某项目按阶段分组的资料列表"""
    try:
        r = requests.get(f"{API_BASE}/projects/{project_id}/documents/by-stage", timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        pass
    return None

def api_documents_summarize(project_id, question):
    """调用后端项目资料总结接口（读资料摘要表，完整总结，不漏文件）"""
    try:
        resp = requests.post(
            f"{API_BASE}/projects/{project_id}/documents/summarize",
            json={"question": question},
            timeout=600,
        )
        return resp
    except Exception as e:
        st.error(f"资料总结请求失败：{e}")
        return None


def api_ask(question, top_k=40, project_id=None, sources=None):
    """调用后端 /ask 智能问答接口（回答较长时会自动续写，超时放宽到 600s）"""
    try:
        payload = {"question": question, "top_k": top_k, "project_id": project_id}
        if sources:
            payload["sources"] = sources
        resp = requests.post(
            f"{API_BASE}/ask",
            json=payload,
            timeout=600,
        )
        return resp
    except Exception as e:
        st.error(f"智能问答请求失败：{e}")
        return None


def api_cross_project(question):
    """调用后端 /cross-project/analyze 跨项目统计与报告接口"""
    try:
        resp = requests.post(
            f"{API_BASE}/cross-project/analyze",
            json={"question": question},
            timeout=600,
        )
        return resp
    except Exception as e:
        st.error(f"跨项目分析请求失败：{e}")
        return None


def render_ask_page():
    st.header("🤖 智能问答")
    st.caption("基于知识库内容提问，由 DeepSeek 生成答案（依据文档资料与合同/经费/成果/待办等结构化台账）")

    # 启动时从本地文件读取历史，避免刷新/重开后丢失
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = _load_chat_history()

    # ---- 选择要提问的项目 ----
    projects = api_list_projects()
    project_options = {f"{p['name']}（ID:{p['id']}）": p["id"] for p in projects}
    option_names = ["🌐 全部项目"] + list(project_options.keys())
    selected = st.selectbox(
        "选择要提问的项目",
        option_names,
        help="选择后，问答只会在该项目的数据里查找",
    )
    selected_project_id = None if selected == "🌐 全部项目" else project_options[selected]

    # ---- 问答范围多选 + 智能自动判断开关 ----
    selected_labels = st.multiselect(
        "问答范围",
        list(QA_SOURCE_OPTIONS.keys()),
        default=list(QA_SOURCE_OPTIONS.keys()),
        help="选择要检索的数据源；开启「智能自动判断范围」时忽略此选择，由后端检索全部数据源",
    )
    auto_scope = st.toggle("智能自动判断范围", value=True)
    sources = None if auto_scope else [QA_SOURCE_OPTIONS[l] for l in selected_labels]

    question = st.text_input("请输入你的问题：", key="ask_input")

    col1, col2 = st.columns(2)
    with col1:
        submitted = st.button("🙋 提问", key="ask_btn")
    with col2:
        clear_clicked = st.button("🗑️ 清空记录", key="clear_btn")

    if clear_clicked:
        st.session_state.chat_history = []
        _save_chat_history(st.session_state.chat_history)
        st.success("聊天记录已清空")

    if submitted:
        q = question.strip()
        if not q:
            st.warning("请先输入问题")
        else:
            with st.spinner("正在检索资料并生成答案，请稍候……"):
                resp = api_ask(q, top_k=40, project_id=selected_project_id, sources=sources)

            if resp is None:
                pass  # 错误已在 api_ask 中提示
            elif resp.status_code != 200:
                st.error(f"问答失败：{resp.text}")
            else:
                data = resp.json()
                answer = data.get("answer", "")
                references = data.get("references", [])
                stat_note = data.get("stat_note", "")
                st.session_state.chat_history.append({
                    "question": q,
                    "answer": answer,
                    "references": references,
                    "stat_note": stat_note,
                })
                _save_chat_history(st.session_state.chat_history)

    # 显示聊天历史（最新在前，不用往下滚动）
    if not st.session_state.chat_history:
        st.info("还没有提问记录，试试问一个知识库里已有的问题吧。")
    for h_idx, item in enumerate(reversed(st.session_state.chat_history)):
        st.markdown(f"**🙋 你：** {item['question']}")
        st.markdown(f"**🤖 AI：** {item['answer']}")
        if item.get("stat_note"):
            st.caption(f"📐 统计口径：{item['stat_note']}")
        if item.get("references"):
            st.markdown("**📎 参考资料：**")
            for r_idx, ref in enumerate(item["references"]):
                stype = ref.get("source_type", "file")
                label = ref.get("label") or ref.get("original_name", "未命名")
                dl = ref.get("download_url", "")
                jump = ref.get("jump_url", "")
                pid = ref.get("project_id")
                st.markdown(f"- {ICON.get(stype, '📄')} **{label}**")
                _c_dl, _c_jump, _c_spacer = st.columns([1, 1, 6])
                with _c_dl:
                    if stype in ("contract", "achievement", "file", "document_summary") and dl:
                        tok = st.session_state.get("token", "")
                        st.markdown(f"[⬇️ 下载]({API_BASE}{dl}?token={tok})")
                with _c_jump:
                    if jump:
                        target_menu = _todo_label if stype == "todo" else jump
                        if st.button("🔗 查看", key=f"qa_jump_{h_idx}_{r_idx}"):
                            st.session_state["_nav_target"] = (target_menu, pid)
                            st.rerun()
                if ref.get("snippet"):
                    st.caption(f"　　片段：{ref['snippet']}")
        st.divider()


def _highlight_keyword(text, keyword):
    """把片段中的关键词用 <mark> 高亮（大小写不敏感），并保留换行"""
    if not text:
        return ""
    # 1. 转义特殊字符，防止被 markdown 误解析
    safe_text = html.escape(text).replace("\n", "<br>")
    if not keyword:
        return safe_text
    safe_kw = html.escape(keyword)
    # 2. 大小写不敏感地匹配关键词
    pattern = re.compile(re.escape(safe_kw), re.IGNORECASE)
    # 3. 把匹配到的词包上 <mark> 标签
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe_text)


def _status_donut_svg(by_status):
    """根据状态统计 {状态: 数量} 生成 SVG 环形图，返回 SVG 字符串。"""
    colors = {
        "正常": "#16a34a",
        "即将到期": "#f59e0b",
        "已超期": "#e53935",
        "未识别": "#9ca3af",
    }
    total = sum(by_status.values()) if by_status else 0
    if total <= 0:
        return ('<svg viewBox="0 0 160 160" xmlns="http://www.w3.org/2000/svg">'
                '<circle cx="80" cy="80" r="60" fill="none" stroke="#e5e7eb" stroke-width="26"/>'
                '<text x="80" y="80" text-anchor="middle" dominant-baseline="central" '
                'font-size="22" font-weight="bold" fill="#1f2937">0</text></svg>')

    R = 60
    CIRC = 2 * 3.1415926 * R
    segments = []
    offset = 0.0
    for status, n in by_status.items():
        if n <= 0:
            continue
        frac = n / total
        dash = frac * CIRC
        color = colors.get(status, "#9ca3af")
        segments.append(
            f'<circle cx="80" cy="80" r="{R}" fill="none" stroke="{color}" stroke-width="26" '
            f'stroke-dasharray="{dash:.2f} {CIRC - dash:.2f}" stroke-dashoffset="{-offset:.2f}" />'
        )
        offset += dash
    center = (f'<text x="80" y="76" text-anchor="middle" dominant-baseline="central" '
              f'font-size="24" font-weight="bold" fill="#1f2937">{total}</text>'
              f'<text x="80" y="96" text-anchor="middle" dominant-baseline="central" '
              f'font-size="11" fill="#6b7280">份合同</text>')
    return (f'<svg viewBox="0 0 160 160" xmlns="http://www.w3.org/2000/svg">'
            f'<g transform="rotate(-90 80 80)">{"".join(segments)}</g>{center}</svg>')


def render_contract_page():
    st.header("📑 合同管理")
    st.caption("上传的文件均为合同，系统自动抽取履约时间、金额、工作内容等关键字段，并做宏观图表化展示")

    # ===== 从文件管理导入（文件库中已分类为「合同」的文件一键建档）=====
    with st.container(border=True):
        st.subheader("📥 从文件管理导入合同")
        st.caption("把「文件管理」中已识别为「合同」的文件一键导入到合同台账（自动带出所属项目，后台解析金额/节点/质保金等字段）")
        if st.button("🔍 扫描文件库中的合同", key="contract_import_scan"):
            cand = api_contract_import_candidates()
            if cand is not None:
                st.session_state["contract_import_candidates"] = cand
        cand = st.session_state.get("contract_import_candidates")
        if cand is not None:
            total = cand.get("total", 0)
            imported = cand.get("imported", 0)
            pending = cand.get("pending", 0)
            st.caption(f"共 {total} 份合同类文件：已导入 {imported} 份，待导入 {pending} 份")
            candidates = cand.get("candidates", [])
            if not candidates:
                st.info("文件管理中暂无「合同」类文件。")
            else:
                pending_items = [c for c in candidates if not c["imported"]]
                if not pending_items:
                    st.success("所有合同类文件都已导入台账 ✅")
                else:
                    for c in pending_items:
                        st.checkbox(
                            f"{c['project_name']} ｜ {c['original_name']}",
                            key=f"contract_import_sel_{c['file_id']}",
                            value=True,
                        )
                    if st.button("📥 导入所选合同到台账", key="contract_import_go", use_container_width=True):
                        sel_ids = [c["file_id"] for c in pending_items if st.session_state.get(f"contract_import_sel_{c['file_id']}", True)]
                        if not sel_ids:
                            st.warning("请勾选要导入的合同")
                        else:
                            with st.spinner("正在导入合同……"):
                                r = api_contract_import(sel_ids)
                            if r and r.status_code == 200:
                                d = r.json()
                                st.success(f"成功导入 {d.get('imported_count', 0)} 份合同，正在后台解析字段（可稍后刷新查看）")
                                st.session_state.pop("contract_import_candidates", None)
                                st.rerun()
                            elif r is not None:
                                st.error(r.text)
                imported_items = [c for c in candidates if c["imported"]]
                if imported_items:
                    with st.expander(f"已导入的合同（{len(imported_items)} 份）"):
                        for c in imported_items:
                            st.markdown(f"- ✅ {c['project_name']} ｜ {c['original_name']}")

    st.divider()

    # ===== 上传区 =====
    with st.container(border=True):
        st.subheader("⬆️ 上传合同")
        # 关联项目（1 项目 → 多合同）
        _projs = api_list_projects()
        _proj_opts = {"（不关联项目）": None}
        _proj_opts.update({f"{p['name']}（ID:{p['id']}）": p["id"] for p in _projs})
        upload_project_id = st.selectbox("关联项目（可选）", list(_proj_opts.keys()), key="contract_upload_project")
        uploaded = st.file_uploader(
            "选择合同文件（支持 PDF / Word / Excel / 图片等，可多选）",
            accept_multiple_files=True,
            key="contract_uploader",
        )
        if uploaded:
            if st.button("📤 上传并解析", key="contract_upload_btn"):
                with st.spinner("正在上传合同文件……"):
                    resp = api_upload_contracts(uploaded, _proj_opts[upload_project_id])
                if resp is None:
                    st.error("上传失败，请检查后端是否运行。")
                elif resp.status_code != 200:
                    st.error(f"上传失败：{resp.text}")
                else:
                    data = resp.json()
                    ok = data.get("uploaded", [])
                    err = data.get("errors", [])
                    if ok:
                        st.success(f"已上传 {len(ok)} 份合同，正在后台解析关键字段（扫描件 OCR 较慢，请稍候刷新查看）")
                    if err:
                        st.warning(f"{len(err)} 份合同处理失败：{'；'.join(e.get('filename', '') for e in err)}")
                    st.rerun()

    st.divider()

    # ===== 按项目筛选 + 时间维度 + 时段筛选 =====
    _proj_filter = {"（全部项目）": None}
    _proj_filter.update({f"{p['name']}（ID:{p['id']}）": p["id"] for p in _projs})
    _filter_labels = list(_proj_filter.keys())
    _cpid = st.session_state.get("current_project_id")
    if _cpid is not None and st.session_state.get("_contract_filter_for") != _cpid:
        _target = next((lbl for lbl, pid in _proj_filter.items() if pid == _cpid), "（全部项目）")
        st.session_state["contract_proj_filter"] = _target
        st.session_state["_contract_filter_for"] = _cpid
    sel_proj_filter = st.selectbox("按项目筛选", _filter_labels, key="contract_proj_filter")
    filter_pid = _proj_filter[sel_proj_filter]

    date_dim = st.radio("时间维度", ["签订日期", "到期日"], horizontal=True, key="contract_date_dim")
    start, end = time_filter_widget("contract")
    date_field = "sign_date" if date_dim == "签订日期" else "due_date"

    stats = api_contract_stats(start=start, end=end, date_field=date_field, project_id=filter_pid)
    if not stats or stats.get("total_count", 0) == 0:
        st.info("暂无合同数据（可能受项目/时间筛选影响），请先上传合同文件或调整筛选条件。")
        return

    # ===== KPI 指标 =====
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("合同总数", stats.get("total_count", 0))
    c2.metric("合同总金额", f"¥{stats.get('total_amount', 0):,.2f}")
    c3.metric("已超期", len(stats.get("overdue", [])))
    c4.metric("即将到期(30天内)", len(stats.get("upcoming", [])))

    st.divider()

    # ===== 超期/临期提醒 =====
    overdue = stats.get("overdue", [])
    upcoming = stats.get("upcoming", [])
    if overdue:
        st.error(f"⚠️ 有 {len(overdue)} 份合同已超期，请及时处理（可点「忽略」不再提示）：")
        for c in overdue:
            days = abs(c["days_left"]) if c["days_left"] is not None else "?"
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.markdown(f"**{c['original_name']}** ｜ 到期日：{c['due_date'] or '未知'}（已超期 {days} 天）")
            if c2.button("🔕 忽略", key=f"od_ignore_{c['id']}", help="忽略该合同的风险预警，下次不再显示"):
                r = api_ignore_contract_alert(c["id"])
                if r and r.status_code == 200:
                    st.rerun()
                elif r is not None:
                    st.error(r.text)
            if c3.button("🗑️ 删除", key=f"od_del_{c['id']}", help="删除该超期合同"):
                r = api_delete_contract(c["id"])
                if r.status_code == 200:
                    st.success(f"已删除：{c['original_name']}")
                    st.rerun()
                else:
                    st.error("删除失败")
        # 一键清除全部超期合同
        with st.container(border=True):
            st.markdown("**🗑️ 一键清除全部超期合同**")
            confirm = st.checkbox("我确认删除以上所有超期合同（不可恢复）", key="confirm_del_overdue")
            if st.button("删除全部超期合同", key="batch_del_overdue", disabled=not confirm):
                ids = [c["id"] for c in overdue]
                r = api_batch_delete_contracts(ids)
                if r and r.status_code == 200:
                    d = r.json()
                    st.success(f"已删除 {d['count']} 份超期合同")
                    st.rerun()
                else:
                    st.error("批量删除失败")
    if upcoming:
        st.warning(f"⏰ 有 {len(upcoming)} 份合同即将到期（可点「忽略」不再提示）：")
        for c in upcoming:
            c1, c2 = st.columns([7, 1])
            c1.markdown(f"- **{c['original_name']}** ｜ 到期日：{c['due_date'] or '未知'}（剩余 {c['days_left']} 天）")
            if c2.button("🔕 忽略", key=f"up_ignore_{c['id']}", help="忽略该合同的风险预警，下次不再显示"):
                r = api_ignore_contract_alert(c["id"])
                if r and r.status_code == 200:
                    st.rerun()
                elif r is not None:
                    st.error(r.text)

    st.divider()

    # ===== 合同明细 =====
    st.subheader("📋 合同明细")
    items = stats.get("items", [])
    if items:
        import pandas as pd
        rows = []
        for c in items:
            ps = c.get("processing_status", "done")
            ps_label = {"done": "✅ 已解析", "processing": "⏳ 解析中", "error": "⚠️ 失败"}.get(ps, ps)
            rows.append({
                "ID": c["id"],
                "解析状态": ps_label,
                "合同名称": _contract_display_name(c),
                "合同编号": c["contract_no"] or "",
                "甲方": c["party_a"] or "",
                "乙方": c["party_b"] or "",
                "类型": c["contract_type"] or "",
                "金额": c["amount_value"] or 0,
                "税率": f"{(c.get('tax_rate') or 0) * 100:.1f}%",
                "含税金额": c.get("amount_incl_tax") or 0,
                "质保金比例": f"{(c.get('warranty_ratio') or 0) * 100:.1f}%",
                "签订日期": c["sign_date"] or "",
                "到期日": c["due_date"] or "",
                "状态": c["status"],
                "剩余天数": c["days_left"] if c["days_left"] is not None else "",
            })
        df = pd.DataFrame(rows)
        names = [_contract_display_label(c, with_project=False) for c in items]
        evt = st.dataframe(
            df, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key="contract_table",
        )
        if evt.selection and evt.selection.rows:
            row_idx = evt.selection.rows[0]
            if 0 <= row_idx < len(items):
                sel_id = items[row_idx]["id"]
                target = next((n for n in names if n.startswith(f"#{sel_id}｜")), None)
                if target:
                    st.session_state["contract_select"] = target
                    st.rerun()

    st.divider()

    # ===== 详情 / 下载 / 删除 =====
    st.subheader("🔎 合同详情与操作")
    names = [_contract_display_label(c, with_project=False) for c in items]
    if names:
        sel = st.selectbox("选择合同", names, key="contract_select")
        c = items[names.index(sel)]
        with st.container(border=True):
            st.markdown(f"### {_contract_display_name(c)}")
            ps = c.get("processing_status", "done")
            if ps == "processing":
                st.info("⏳ 该合同正在后台解析中（扫描件 OCR 较慢），请稍后刷新页面查看已抽取的字段。")
            elif ps == "error":
                st.warning("⚠️ 该合同解析失败，可删除后重新上传。")
            col_a, col_b = st.columns(2)
            col_a.markdown(f"**合同编号**：{c['contract_no'] or '未识别'}")
            col_a.markdown(f"**甲方**：{c['party_a'] or '未识别'}")
            col_a.markdown(f"**乙方**：{c['party_b'] or '未识别'}")
            col_a.markdown(f"**类型**：{c['contract_type'] or '未识别'}")
            if c["amount_value"]:
                col_b.markdown(f"**金额**：{c['currency'] or '¥'} {c['amount_value']:,.2f}")
            else:
                col_b.markdown("**金额**：未识别")
            col_b.markdown(f"**签订日期**：{c['sign_date'] or '未识别'}")
            col_b.markdown(f"**履约起期**：{c['start_date'] or '未识别'}")
            col_b.markdown(f"**到期日**：{c['due_date'] or '未识别'}（{c['status']}）")
            if c["service_content"]:
                st.markdown("**工作内容**：")
                st.write(c["service_content"])

            # 金额拆分 + 质保金
            st.markdown("**💰 金额拆分与质保金**")
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("不含税", f"¥{c.get('amount_ex_tax') or 0:,.2f}")
            a2.metric("税率", f"{(c.get('tax_rate') or 0) * 100:.1f}%")
            a3.metric("税额", f"¥{c.get('tax_amount') or 0:,.2f}")
            a4.metric("含税金额", f"¥{c.get('amount_incl_tax') or 0:,.2f}")
            if c.get("warranty_ratio") or c.get("warranty_period") or c.get("warranty_due_date"):
                st.markdown(
                    f"**🔐 质保金**：比例 {(c.get('warranty_ratio') or 0) * 100:.1f}% ｜ 到期时长 {c.get('warranty_period') or '—'} ｜ 到期日 {c.get('warranty_due_date') or '—'}"
                )

            # 财务执行（暂估/结算/发票 + 差异 + 超额判断）
            st.markdown("**💵 财务执行（暂估 / 结算 / 发票）**")
            cmp = api_funding_compare(c["project_id"])
            fc = None
            if cmp:
                for cc in cmp.get("contracts", []):
                    if cc.get("id") == c["id"]:
                        fc = cc
                        break
            if fc:
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("暂估", fmt_money(fc.get("estimate_total")))
                f2.metric("结算", fmt_money(fc.get("settlement_total")))
                f3.metric("发票", fmt_money(fc.get("invoice_total")))
                f4.metric("最终", fmt_money(fc.get("effective_total")))
                diff = fc.get("diff_vs_contract")
                if diff is not None:
                    tol = float((cmp.get("config") or {}).get("overrun_tolerance", 0) or 0)
                    if diff > 0:
                        st.error(f"⚠️ 超额：实际较合同多 {fmt_money(diff)}（容忍比例 {tol * 100:.1f}%）")
                    else:
                        st.success(f"✅ 未超额：差异 {fmt_money(diff)}")
                else:
                    st.caption("差异：—（口径为「三列并存」，无单一最终金额）")
            else:
                st.caption("该合同暂无财务执行数据（请先在经费管理上传暂估/结算/发票）。")

            # 人工确认生效
            st.markdown("**✅ 人工确认**（抽取结果须经人工核对后方可生效）")
            if c.get("confirmed"):
                st.success("已人工确认生效")
            else:
                st.warning("尚未人工确认，执行计划节点处于「待确认」状态")
            cc1, cc2 = st.columns([3, 1])
            _proj_list = api_list_projects()
            _cproj_opts = {"（不关联项目）": None}
            _cproj_opts.update({f"{p['name']}（ID:{p['id']}）": p["id"] for p in _proj_list})
            _cur_label = next((k for k, v in _cproj_opts.items() if v == c.get("project_id")), "（不关联项目）")
            sel_proj = cc1.selectbox("关联项目", list(_cproj_opts.keys()),
                                     index=list(_cproj_opts.keys()).index(_cur_label), key=f"ct_proj_{c['id']}")
            if cc2.button("✅ 确认生效", key=f"ct_confirm_{c['id']}", disabled=bool(c.get("confirmed"))):
                r = api_confirm_contract(c["id"], _cproj_opts[sel_proj])
                if r and r.status_code == 200:
                    st.success("已确认生效，执行计划节点已激活")
                    st.rerun()
                elif r is not None:
                    st.error(r.text)

            dl_col, view_col, del_col = st.columns(3)
            r_dl = api_download_contract(c["id"])
            if r_dl.status_code == 200:
                dl_col.download_button(
                    "⬇️ 下载合同",
                    data=r_dl.content,
                    file_name=c["original_name"],
                    mime="application/octet-stream",
                    key=f"dl_{c['id']}",
                )
            else:
                dl_col.warning("下载失败")
            with view_col:
                with st.expander("👁️ 查看提取文本"):
                    r_c = api_contract_content(c["id"])
                    if r_c.status_code == 200:
                        st.text_area("文本", r_c.json().get("content", ""), height=260, key=f"ctxt_{c['id']}")
                    else:
                        st.warning("获取内容失败")
            if del_col.button("🗑️ 删除", key=f"del_{c['id']}"):
                r_del = api_delete_contract(c["id"])
                if r_del.status_code == 200:
                    st.success(r_del.json().get("message", "已删除"))
                    st.rerun()
                else:
                    st.error("删除失败")

        # ===== 执行计划节点（付款节点 + 业务节点）+ 变更追溯 =====
        nodes_data = api_contract_nodes(c["id"])
        if nodes_data:
            with st.container(border=True):
                st.markdown("### 🗓️ 执行计划（付款节点 + 业务节点）")
                st.caption("到达节点自动推送待办；修改节点会记录修改时间与原因，全程可追溯")
                nodes = nodes_data.get("nodes", [])
                changes = nodes_data.get("changes", [])
                if not nodes:
                    st.info("暂无执行计划节点（合同未解析出付款/业务节点，或尚未解析完成）")
                else:
                    for n in nodes:
                        ntype = "💰 付款" if n["node_type"] == "payment" else "📋 业务"
                        status_icon = {"已完成": "✅", "已取消": "❌", "待办": "⏳", "待确认": "🕓"}.get(n.get("status"), "⏳")
                        with st.expander(
                            f"{status_icon} {ntype} ｜ {n['node_name']} ｜ 计划 {n.get('planned_date') or '无日期'} ｜ {n.get('status')}"
                        ):
                            with st.form(f"node_form_{n['id']}"):
                                nc1, nc2, nc3 = st.columns(3)
                                new_name = nc1.text_input("节点名称", value=n["node_name"])
                                new_date = nc2.text_input("计划日期", value=n.get("planned_date") or "", placeholder="YYYY-MM-DD")
                                new_amount = nc3.number_input("金额（付款节点）", value=float(n.get("amount") or 0),
                                                               step=1000.0, format="%.2f")
                                reason = st.text_input("修改原因（必填，用于追溯）", placeholder="如：双方协商调整付款时间")
                                submit = st.form_submit_button("💾 保存修改")
                            if submit:
                                if not reason.strip():
                                    st.warning("请填写修改原因")
                                else:
                                    r = api_update_contract_node(n["id"], {
                                        "node_name": new_name, "planned_date": new_date,
                                        "amount": new_amount, "change_reason": reason,
                                    })
                                    if r and r.status_code == 200:
                                        st.success("已更新并记录变更")
                                        st.rerun()
                                    elif r is not None:
                                        st.error(r.text)
                            st.markdown("**标记状态**")
                            sc = st.columns(3)
                            for _i, _lbl in enumerate(["待办", "已完成", "已取消"]):
                                if sc[_i].button(f"设为 {_lbl}", key=f"node_st_{n['id']}_{_lbl}"):
                                    r = api_update_contract_node_status(n["id"], {"status": _lbl, "change_reason": "手动更新状态"})
                                    if r and r.status_code == 200:
                                        st.rerun()
                    if changes:
                        st.markdown("**📜 变更记录（全程可追溯）**")
                        import pandas as pd
                        ch_rows = [{
                            "时间": ch.get("changed_at", ""), "字段": ch.get("field", ""),
                            "原值": ch.get("old_value", ""), "新值": ch.get("new_value", ""),
                            "原因": ch.get("change_reason", ""),
                        } for ch in changes]
                        st.dataframe(pd.DataFrame(ch_rows), use_container_width=True, hide_index=True)

    st.divider()

    # ===== 查重去重（名称 + 内容双重判断）=====
    st.subheader("🔍 查重去重")
    st.caption("按「文件名称 + 合同内容」双重判断识别重复合同，每组只保留最新一份")
    if st.button("🔍 扫描重复合同", key="contract_dup_scan"):
        dup = api_contract_duplicates()
        if dup is not None:
            st.session_state["contract_dup_result"] = dup
    dup_result = st.session_state.get("contract_dup_result")
    if dup_result is not None:
        groups = dup_result.get("groups", [])
        total_remove = dup_result.get("total_remove", 0)
        if not groups:
            st.success("未发现重复合同 ✅")
        else:
            st.warning(f"发现 {len(groups)} 组重复，共 {total_remove} 份冗余合同可删除")
            for g in groups:
                keep = g["keep"]
                with st.expander(f"重复组：{g['key']}（{g['count']} 份）"):
                    st.markdown(f"**✅ 保留（最新）**：`{_contract_display_name(keep)}`（ID #{keep['id']}｜{keep.get('created_at', '')}）")
                    for r in g["remove"]:
                        st.markdown(f"　🗑️ 删除：`{_contract_display_name(r)}`（ID #{r['id']}｜{r.get('created_at', '')}）")
            confirm = st.checkbox("我确认删除以上重复合同（每组保留最新一份）", key="contract_dup_confirm")
            if st.button("⚠️ 一键去重", key="contract_dup_go", disabled=not confirm):
                res = api_contract_deduplicate()
                if res is not None:
                    st.success(f"已删除 {res.get('count', 0)} 份重复合同")
                    st.session_state.pop("contract_dup_result", None)
                    st.rerun()


# ============ 页面主体 ============
# 登录拦截：未登录时只显示登录页
if not st.session_state.get("token"):
    st.markdown("<div style='height:4rem'></div>", unsafe_allow_html=True)
    lc0, lc1, lc2 = st.columns([1, 1.15, 1])
    with lc1:
        with st.container(border=True):
            st.markdown("## 🔐 登录")
            st.caption("研发知识智能管理平台 · 请输入管理员账号密码")
            with st.form("login_form"):
                username = st.text_input("用户名", value="admin")
                password = st.text_input("密码", type="password")
                submitted = st.form_submit_button("登 录", type="primary", use_container_width=True)
            if submitted:
                if not username or not password:
                    st.warning("请输入用户名和密码")
                else:
                    resp = api_login(username, password)
                    if resp is None:
                        st.error("无法连接后端服务，请先运行 start_all.bat 启动后端。")
                    elif resp.status_code == 200:
                        data = resp.json()
                        st.session_state["token"] = data["token"]
                        st.session_state["username"] = data["username"]
                        st.session_state["expires_at"] = data.get("expires_at", int(time.time()) + 7 * 24 * 3600)
                        _apply_auth()
                        st.rerun()
                    else:
                        try:
                            detail = resp.json().get("detail", "登录失败")
                        except Exception:
                            detail = "登录失败"
                        st.error(detail)
    st.stop()

# 已登录：注入 token 到请求头，并在到期前自动续期
_apply_auth()
_maybe_refresh_token()

# 平台主标题已移至侧边栏顶部品牌区（避免与各页面标题重复，主内容区更聚焦）

# 待办未处理数量（用于导航栏角标，每次重跑刷新）
_todo_unhandled = api_todo_count()
_todo_label = "✅ 待办模块" + (f"（{_todo_unhandled}）" if _todo_unhandled else "")

NAV_OPTIONS = ["🏠 项目列表", "➕ 创建项目", "📁 文件管理", "📌 项目管理", "📑 合同管理", "💰 经费管理", "🏆 成果台账", _todo_label, "📊 数据大屏", "🔍 全文搜索", "🤖 智能问答", "🌐 跨项目分析", "🔐 审计日志", "💾 系统备份"]

# ---- 处理"点击项目跳转"的待办导航：必须在 radio 实例化之前修改 nav_menu ----
if st.session_state.get("_nav_target"):
    target_menu, target_pid = st.session_state.pop("_nav_target")
    st.session_state.nav_menu = target_menu
    if target_pid is not None:
        st.session_state.current_project_id = target_pid
        st.session_state.pop("file_project_select", None)   # 强制文件管理下拉框重新选中该项目

if "nav_menu" not in st.session_state:
    st.session_state.nav_menu = NAV_OPTIONS[0]

st.sidebar.markdown('<div class="sidebar-brand">📚 科技项目智能管理平台</div>', unsafe_allow_html=True)
st.sidebar.caption("研发知识 · 项目 · 合同 · 成果 · 经费")
st.sidebar.markdown("---")
menu = st.sidebar.radio("导航", NAV_OPTIONS, key="nav_menu")

st.sidebar.markdown("---")
st.sidebar.markdown(f"👤 已登录：**{st.session_state.get('username', '')}**")
if st.sidebar.button("🔒 退出登录", use_container_width=True):
    st.session_state.pop("token", None)
    st.session_state.pop("username", None)
    st.session_state.pop("expires_at", None)
    st.rerun()

with st.sidebar.expander("🔑 修改密码"):
    with st.form("change_pwd_form"):
        old_pwd = st.text_input("旧密码", type="password", key="old_pwd")
        new_pwd = st.text_input("新密码（至少 6 位）", type="password", key="new_pwd")
        new_pwd2 = st.text_input("确认新密码", type="password", key="new_pwd2")
        submit_pwd = st.form_submit_button("确认修改", use_container_width=True)
    if submit_pwd:
        if not old_pwd or not new_pwd:
            st.sidebar.warning("请填写完整")
        elif new_pwd != new_pwd2:
            st.sidebar.error("两次输入的新密码不一致")
        elif len(new_pwd) < 6:
            st.sidebar.error("新密码至少 6 位")
        else:
            resp = api_change_password(st.session_state.get("username", "admin"), old_pwd, new_pwd)
            if resp is None:
                st.sidebar.error("无法连接后端")
            elif resp.status_code == 200:
                st.session_state.pop("token", None)
                st.session_state.pop("username", None)
                st.session_state.pop("expires_at", None)
                st.sidebar.success("密码已修改，请用新密码重新登录")
                st.rerun()
            else:
                try:
                    st.sidebar.error(resp.json().get("detail", "修改失败"))
                except Exception:
                    st.sidebar.error("修改失败")



# ============ 页面1：项目列表 ============
if menu == "🏠 项目列表":
    st.header("项目列表")
    projects = api_list_projects()

    # ===== 项目概览框（名字 + 编号 + 描述）=====
    st.subheader("📌 项目概览")
    with st.container(border=True):
        if not projects:
            st.write("（还没有项目）")
        else:
            for i, p in enumerate(projects, 1):
                pcode = p.get("code", "")
                pdesc = p.get("description", "") or "（无描述）"
                c1, c2, c3 = st.columns([6.5, 1.2, 1])
                c1.write(f"{i}. 📁 {p['name']} ｜ 编号：{pcode} ｜ 描述：{pdesc}")
                if c2.button("📂 进入", key=f"enter_{p['id']}", help=f"进入项目「{p['name']}」"):
                    st.session_state["_nav_target"] = ("📁 文件管理", p["id"])
                    st.rerun()
                if c3.button("🗑️ 删除", key=f"delbtn_{p['id']}", help="删除该项目"):
                    st.session_state["confirm_del_project"] = p["id"]

    # 二次确认：点击上面的"删除"后，这里会出现确认/取消按钮
    if "confirm_del_project" in st.session_state and st.session_state["confirm_del_project"] is not None:
        pid = st.session_state["confirm_del_project"]
        st.warning(f"⚠️ 确定要删除项目 ID={pid} 吗？该项目下的所有文件都会一并删除，且无法恢复！")
        col_ok, col_cancel = st.columns([1, 1])
        if col_ok.button("✅ 确认删除", key="confirm_del_yes"):
            resp = api_delete_project(pid)
            if resp.status_code == 200:
                st.success(resp.json().get("message", "删除成功"))
                st.session_state["confirm_del_project"] = None
                st.rerun()
            else:
                st.error(f"删除失败：{resp.text}")
        if col_cancel.button("❌ 取消", key="confirm_del_no"):
            st.session_state["confirm_del_project"] = None
            st.rerun()

    st.divider()


    if not projects:
        st.info("还没有任何项目，点击左侧「➕ 创建项目」开始吧！")
    
# ============ 页面2：创建项目 ============
elif menu == "➕ 创建项目":
    st.header("创建新项目")

    with st.form("create_project_form"):
        code = st.text_input("项目编号（必填，唯一）", placeholder="例如：P002")
        name = st.text_input("项目名称（必填）", placeholder="例如：科技项目名称")
        description = st.text_area("项目描述（可选）", placeholder="简单介绍一下这个项目……")
        stage = st.selectbox("项目阶段", PROJECT_STAGES, index=0)
        submitted = st.form_submit_button("✅ 创建项目")

    if submitted:
        if not code or not name:
            st.error("项目编号和项目名称是必填的！")
        else:
            resp = api_create_project(code, name, description, stage)
            if resp.status_code == 200:
                st.success(f"项目「{name}」创建成功！")
            else:
                st.error(f"创建失败：{resp.json().get('detail', resp.text)}")


# ============ 页面3：文件管理 ============
elif menu == "📁 文件管理":
    st.header("文件管理")

    # —— 一次性分类修正：按「合同仅 Word/PDF」白名单 + 财务单据关键词，纠正现存文件分类 ——
    with st.expander("🔧 分类修正工具（合同格式白名单 / 财务单据标签）", expanded=False):
        st.caption("一键纠正现存文件的分类：非 Word/PDF 却标为「合同」的文件会改为「财务单据」或「附件」；"
                   "文件名含发票/结算单/暂估单/估算单等关键词的文件会自动打上「财务单据」标签。")
        if st.button("🛠️ 执行分类修正", key="btn_correct_classifications", use_container_width=True):
            with st.spinner("正在修正文件分类……"):
                r = api_correct_classifications()
            if r and r.status_code == 200:
                d = r.json()
                _msg = (
                    f"修正完成：纠正非合同文件 {d.get('fixed_non_contract_files', 0)} 份，"
                    f"新增财务单据标签 {d.get('tagged_financial_files', 0)} 份。"
                )
                removed = d.get("removed_contracts", [])
                if removed:
                    _msg += f"已从合同台账移除 {len(removed)} 份非 Word/PDF 记录。"
                st.success(_msg)
                bad = d.get("bad_contract_ledger", [])
                if bad:
                    st.warning(
                        f"合同台账中仍有 {len(bad)} 份非 Word/PDF 记录（含关联数据，未自动删除，请到「合同管理」手动处理）："
                        + "；".join(f"#{x['id']} {x['original_name']}" for x in bad)
                    )
                st.rerun()
            elif r is not None:
                st.error(f"修正失败：{r.text}")

    projects = api_list_projects()
    if not projects:
        st.warning("请先创建项目，再上传文件。")
    else:
        project_options = {f"{p['name']}（ID:{p['id']}）": p["id"] for p in projects}
        option_labels = list(project_options.keys())
        # 默认选中：优先使用"从项目列表跳转"带过来的项目
        if "file_project_select" not in st.session_state:
            default_label = option_labels[0]
            cpid = st.session_state.get("current_project_id")
            if cpid:
                for label, pid in project_options.items():
                    if pid == cpid:
                        default_label = label
                        break
            st.session_state.file_project_select = default_label
        selected = st.selectbox("选择项目", option_labels, key="file_project_select")
        project_id = project_options[selected]

        files = api_list_files(project_id)

        # ===== 上传文件（折叠，无文件时自动展开）=====
        with st.expander("⬆️ 上传文件", expanded=(len(files) == 0)):
            uploaded = st.file_uploader(
                "选择要上传的文件（支持 PDF / Word / Excel / 图片 / 文本）",
                accept_multiple_files=True,
                key="file_uploader_main",
            )
            if uploaded:
                if st.button("上传到当前项目", use_container_width=True):
                    ok_list = []
                    fail_list = []
                    with st.spinner("正在上传……"):
                        for f in uploaded:
                            resp = api_upload_file(project_id, f)
                            if resp.status_code == 200:
                                ok_list.append(f.name)
                            else:
                                fail_list.append(f"{f.name}（{resp.text}）")
                    if ok_list:
                        st.success(f"成功上传 {len(ok_list)} 个文件：{'、'.join(ok_list)}")
                    if fail_list:
                        st.error("以下文件上传失败：")
                        for item in fail_list:
                            st.write(f"- {item}")
                    st.rerun()

        if not files:
            st.info("该项目还没有文件。")
        else:
            # ---- 搜索框：按文件名 / 分类 / 类型 / 阶段 / 简述模糊匹配 ----
            search_kw = st.text_input(
                "🔍 搜索文件",
                key="file_search_kw",
                placeholder="输入关键词，按文件名 / 分类 / 类型 / 简述模糊匹配……",
            )
            kw = (search_kw or "").strip().lower()
            if kw:
                def _searchable(f):
                    return " ".join(
                        str(f.get(k) or "")
                        for k in ("original_name", "category", "doc_type", "stage", "ai_summary")
                    ).lower()
                filtered_files = [f for f in files if kw in _searchable(f)]
            else:
                filtered_files = files
            st.caption(
                f"共 {len(files)} 个文件"
                + (f"　·　🔎 匹配到 {len(filtered_files)} 个" if kw else "")
            )

            # ---- 跨项目全文搜索（按文件正文内容，覆盖所有项目）----
            with st.expander("🔍 跨项目全文搜索（按文件正文内容，覆盖所有项目）", expanded=False):
                ft_q = st.text_input(
                    "搜索关键词",
                    key="ft_search_q",
                    placeholder="输入关键词，在所有项目文件的内容中搜索……",
                )
                if st.button("🔍 全文搜索", key="btn_ft_search", use_container_width=True):
                    q = (ft_q or "").strip()
                    if not q:
                        st.warning("请输入要搜索的关键词。")
                    else:
                        with st.spinner("正在全文搜索（逐份比对文件内容，请稍候）……"):
                            resp = api_search(q)
                        if resp is None:
                            st.error("搜索请求失败，请检查后端是否运行。")
                        elif resp.status_code != 200:
                            st.error(f"搜索失败：{resp.text}")
                        else:
                            st.session_state["ft_results"] = resp.json()
                            st.session_state["ft_query"] = q
                ft_results = st.session_state.get("ft_results")
                if ft_results is not None:
                    ftq = st.session_state.get("ft_query", "")
                    if not ft_results:
                        st.info(f"没有找到包含「{ftq}」的文件。")
                    else:
                        st.success(f"共找到 {len(ft_results)} 个匹配文件")
                        for r in ft_results:
                            with st.container(border=True):
                                st.markdown(f"**📄 {r['original_name']}**")
                                st.caption(f"📁 项目：{r['project_name']}（编号：{r['project_code']}）")
                                if r.get("snippet"):
                                    st.markdown(_highlight_keyword(r["snippet"], ftq), unsafe_allow_html=True)
                                tok = st.session_state.get("token", "")
                                st.markdown(f"[⬇️ 下载]({API_BASE}/files/{r['file_id']}/download?token={tok})")

            if not filtered_files:
                st.info("没有匹配的文件，请尝试更换关键词。")
                st.stop()

            # ---- 批量下载工具栏（复选框选择 + 一键打包下载）----
            st.subheader("⬇️ 批量下载")
            tb1, tb2, tb3, tb4 = st.columns([1, 1, 1.8, 1.8])
            with tb1:
                if st.button("☑️ 全选", key="btn_select_all_files", use_container_width=True):
                    for f in filtered_files:
                        st.session_state[f"sel_{f['id']}"] = True
                    st.rerun()
            with tb2:
                if st.button("⬜ 清空选择", key="btn_clear_all_files", use_container_width=True):
                    for f in filtered_files:
                        st.session_state[f"sel_{f['id']}"] = False
                    st.rerun()

            # 从 session_state 读取已勾选文件（复选框状态在交互时已写入 session_state）
            selected_ids = [f["id"] for f in filtered_files if st.session_state.get(f"sel_{f['id']}", False)]
            all_ids = [f["id"] for f in filtered_files]

            with tb3:
                if st.button(f"📦 下载所选（{len(selected_ids)}）", key="btn_dl_selected", use_container_width=True):
                    if not selected_ids:
                        st.warning("请先勾选要下载的文件。")
                    else:
                        _prepare_zip_download(project_id, selected_ids, f"所选{len(selected_ids)}个文件.zip")
            with tb4:
                if st.button(f"📦 一键下载全部（{len(all_ids)}）", key="btn_dl_all", use_container_width=True):
                    _prepare_zip_download(project_id, all_ids, f"全部{len(all_ids)}个文件.zip")

            # 打包完成后，这里出现下载按钮
            if st.session_state.get("zip_bytes"):
                zc1, zc2 = st.columns([3, 1])
                with zc1:
                    st.download_button(
                        label=f"✅ 打包完成，点击下载（{st.session_state.get('zip_name', '打包.zip')}）",
                        data=st.session_state["zip_bytes"],
                        file_name=st.session_state.get("zip_name", "打包.zip"),
                        mime="application/zip",
                        key="dl_zip_result",
                        use_container_width=True,
                    )
                with zc2:
                    if st.button("🗑️ 清除", key="clear_zip_result", use_container_width=True):
                        st.session_state.pop("zip_bytes", None)
                        st.session_state.pop("zip_name", None)
                        st.rerun()
            st.divider()

            # 每个文件一张卡片：首行=勾选 + 文件名 + 下载 + 删除；折叠区=简述 + 内容预览
            for f in filtered_files:
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([0.5, 5.5, 1, 1])
                    with c1:
                        st.checkbox("选择", key=f"sel_{f['id']}", label_visibility="collapsed")
                    with c2:
                        cat = f.get("category") or ""
                        dtype = f.get("doc_type") or ""
                        pstatus = f.get("processing_status") or "done"
                        tags = [t for t in [cat, dtype] if t]
                        if f.get("amount"):
                            tags.append(f"¥{f['amount']:,.2f}")
                        if pstatus == "processing":
                            tags.append("⏳ 解析中")
                        elif pstatus == "error":
                            tags.append("⚠️ 解析失败")
                        tagstr = f"　`{' ｜ '.join(tags)}`" if tags else ""
                        st.markdown(f"**{f['original_name']}**{tagstr}")
                        st.caption(f"{_file_type(f['original_name'])} ｜ 上传于 {_format_date(f.get('uploaded_at'))}")
                    with c3:
                        resp = api_download_file(f["id"])
                        if resp.status_code == 200:
                            st.download_button(
                                "⬇️ 下载",
                                data=resp.content,
                                file_name=f["original_name"],
                                mime="application/octet-stream",
                                key=f"dl_{f['id']}",
                                use_container_width=True,
                            )
                    with c4:
                        if st.button("🗑️", key=f"del_file_{f['id']}", help="删除该文件", use_container_width=True):
                            resp_del = api_delete_file(f["id"])
                            if resp_del.status_code == 200:
                                st.success(f"文件「{f['original_name']}」已删除")
                                st.rerun()
                            else:
                                st.error(f"删除失败：{resp_del.text}")

                    # 折叠：简述 + 内容预览（默认收起，避免页面冗余）
                    with st.expander("📝 简述 & 内容预览", expanded=False):
                        summary = f.get("ai_summary") or ""
                        st.caption(f"**简述**：{summary or '（暂无摘要）'}")
                        st.divider()
                        resp_content = api_get_content(f["id"])
                        if resp_content.status_code == 200:
                            content = resp_content.json().get("content", "")
                            if content and content.strip():
                                st.text_area("文件内容", content, height=250, key=f"content_{f['id']}")
                            else:
                                st.info("未能提取到文字内容（可能是扫描版 PDF 或图片文件）。")
                        else:
                            st.warning("获取内容失败，请检查后端是否运行。")

# ============ 页面3.5：项目管理（一屏聚合） ============
elif menu == "📌 项目管理":
    st.header("📌 项目管理")
    st.caption("单项目一屏聚合：基本信息、关键指标、四阶段进度与资料分组")

    _pm_projects = api_list_projects()
    if not _pm_projects:
        st.warning("请先创建项目。")
    else:
        _pm_opts = {f"{p['name']}（ID:{p['id']}）": p["id"] for p in _pm_projects}
        _pm_labels = list(_pm_opts.keys())
        # 默认选中：优先使用跳转带来的项目
        if "pm_project_select" not in st.session_state:
            _pm_default = _pm_labels[0]
            _pm_cpid = st.session_state.get("current_project_id")
            if _pm_cpid:
                for _lbl, _pid in _pm_opts.items():
                    if _pid == _pm_cpid:
                        _pm_default = _lbl
                        break
            st.session_state.pm_project_select = _pm_default
        _pm_selected = st.selectbox("选择项目", _pm_labels, key="pm_project_select")
        project_id = _pm_opts[_pm_selected]

        d = api_project_dashboard(project_id)
        if not d:
            st.info("暂无数据或后端未响应。")
        else:
            _proj = d.get("project", {})
            _metrics = d.get("metrics", {})
            _stages = d.get("stages", [])
            _files_by_stage = d.get("files_by_stage", [])

            # ① 信息卡
            with st.container(border=True):
                st.markdown(f"### 📁 {_proj.get('name', '')}")
                st.caption(
                    f"编号：{_proj.get('code', '')}　｜　当前阶段：{_proj.get('stage', '项目立项')}"
                    f"　｜　创建于 {_proj.get('created_at', '')}"
                )
                st.write(_proj.get("description") or "（无描述）")

            # ② 6 指标
            _m1, _m2, _m3, _m4, _m5, _m6 = st.columns(6)
            _m1.metric("文件数", _metrics.get("file_count", 0))
            _m2.metric("合同数", _metrics.get("contract_count", 0))
            _m3.metric("合同总金额", fmt_money(_metrics.get("contract_total_amount", 0)))
            _m4.metric("经费投入", fmt_money(_metrics.get("funding_income", 0)))
            _m5.metric("成果数", _metrics.get("achievement_count", 0))
            _m6.metric("待办数", _metrics.get("todo_count", 0))

            st.divider()

            # ③ 4 阶段进度条
            st.subheader("🚩 四阶段进度")
            st.markdown(stage_progress(_stages, _proj.get("stage", "项目立项")), unsafe_allow_html=True)

            st.divider()

            # ④ 资料按阶段分组
            st.subheader("🗂️ 资料按阶段分组")
            if not _files_by_stage:
                st.info("该项目暂无资料。")
            else:
                import pandas as pd
                _pstatus_map = {"done": "✅ 已完成", "processing": "⏳ 解析中", "error": "⚠️ 解析异常"}
                for _grp in _files_by_stage:
                    _flist = _grp.get("files", [])
                    with st.expander(
                        f"{_grp.get('stage', '未划分')}（{len(_flist)} 份）",
                        expanded=False,
                    ):
                        if not _flist:
                            st.caption("（无资料）")
                        else:
                            _frows = []
                            for _f in _flist:
                                _frows.append({
                                    "文件名": _f.get("original_name", ""),
                                    "类型": _f.get("doc_type") or "",
                                    "分类": _f.get("category") or "",
                                    "AI 摘要": _f.get("ai_summary") or "",
                                    "状态": _pstatus_map.get(_f.get("processing_status"), _f.get("processing_status") or "已完成"),
                                })
                            st.dataframe(pd.DataFrame(_frows), use_container_width=True, hide_index=True)

            st.divider()

            # ⑤ 快捷跳转
            st.subheader("🔗 快捷跳转")
            _j1, _j2, _j3, _j4 = st.columns(4)
            if _j1.button("📑 合同管理", key="pm_jump_contract", use_container_width=True):
                st.session_state["_nav_target"] = ("📑 合同管理", project_id)
                st.session_state["current_project_id"] = project_id
                st.rerun()
            if _j2.button("💰 经费管理", key="pm_jump_funding", use_container_width=True):
                st.session_state["_nav_target"] = ("💰 经费管理", project_id)
                st.session_state["current_project_id"] = project_id
                st.rerun()
            if _j3.button("🏆 成果台账", key="pm_jump_achievement", use_container_width=True):
                st.session_state["_nav_target"] = ("🏆 成果台账", project_id)
                st.session_state["current_project_id"] = project_id
                st.rerun()
            if _j4.button("📁 文件管理", key="pm_jump_file", use_container_width=True):
                st.session_state["_nav_target"] = ("📁 文件管理", project_id)
                st.session_state["current_project_id"] = project_id
                st.rerun()

# ============ 页面4：合同管理 ============
elif menu == "📑 合同管理":
    render_contract_page()

# ============ 页面4.1：经费管理 ============
elif menu == "💰 经费管理":
    st.header("💰 经费管理")
    st.caption("年度经费一览表：按「项目 × 月份」录入投入金额、经费科目、发生日期与支撑材料附件")

    from datetime import datetime as _dt
    import pandas as pd

    # ===== 年度经费一览表（前置到页面顶部：年度选择 → 新增项目行 → 总览 → 矩阵 → 单元格录入 → 行管理）=====
    cur_year = _dt.now().year
    year_opts = list(range(cur_year - 2, cur_year + 3))
    year = st.selectbox("📅 年度", year_opts, index=year_opts.index(cur_year), key="fund_grid_year")

    grid = api_funding_grid(year)
    projects = api_list_projects()
    rows = (grid or {}).get("rows", [])
    cells = (grid or {}).get("cells", {})
    attachments = (grid or {}).get("attachments", {})
    contract_amounts = (grid or {}).get("contract_amounts", {})
    summary = api_funding_grid_summary()
    cross_map = {p["project_id"]: p for p in (summary or {}).get("projects", [])}

    # —— 新增项目行 ——
    with st.container(border=True):
        st.subheader("➕ 新增项目行")
        if not projects:
            st.warning("暂无项目，请先到「项目列表」创建项目。")
        else:
            existing_ids = {r["project_id"] for r in rows}
            avail = [p for p in projects if p["id"] not in existing_ids]
            if not avail:
                st.info("所有项目都已添加。")
            else:
                p_labels = {}
                for p in avail:
                    lbl = f"{p['name']}（ID:{p['id']}）"
                    other_years = [y for y in cross_map.get(p["id"], {}).get("years", []) if y != year]
                    if other_years:
                        lbl += f"　｜已在 {'/'.join(map(str, other_years))} 年有经费"
                    p_labels[lbl] = p["id"]
                c1, c2 = st.columns([4, 1])
                sel_label = c1.selectbox("选择项目", list(p_labels.keys()), key="fund_grid_add_proj")
                if c2.button("➕ 添加", key="fund_grid_add_btn", use_container_width=True):
                    r = api_add_funding_row(year, p_labels[sel_label])
                    if r and r.status_code == 200:
                        st.rerun()
                    elif r is not None:
                        st.error(r.text)

    st.divider()

    # —— 项目全周期经费总览（跨年自动汇总）——
    if summary and summary.get("projects"):
        years_all = summary.get("years_all", [])
        with st.expander(
            f"🔭 项目全周期经费总览（跨年汇总）· {summary['total_count']} 个项目，{summary['cross_year_count']} 个跨年",
            expanded=False,
        ):
            st.caption("跨年项目自动按年度汇总，一屏看清项目从立项到验收的全周期经费投入")
            col_names = ["项目"] + [f"{y} 年" for y in years_all] + ["总计", "跨年"]
            data = []
            for p in summary["projects"]:
                row_vals = [p["project_name"]]
                for y in years_all:
                    v = p["year_totals"].get(str(y), 0)
                    row_vals.append(f"{v:,.0f}" if v else "")
                row_vals.append(f"{p['total']:,.0f}" if p["total"] else "")
                row_vals.append(f"跨 {p['year_count']} 年" if p["is_cross_year"] else "—")
                data.append(row_vals)
            sdf = pd.DataFrame(data, columns=col_names)
            st.dataframe(sdf, use_container_width=True, hide_index=True)

    # —— 矩阵总览 + 合同金额（暂估/结算/到账，可人工修改）——
    st.subheader("📊 年度经费一览表")
    st.caption("横向 1~12 月，纵向为项目；单元格显示当月投入金额，📎 表示已上传支撑材料附件")
    if not rows:
        st.info("请先在上方「新增项目行」添加项目，即可按月份录入经费。")
    else:
        col_names = ["项目"] + [f"{m}月" for m in range(1, 13)] + ["合计"]
        data = []
        for r in rows:
            pid = r["project_id"]
            row_vals = [r["project_name"]]
            total = 0.0
            for m in range(1, 13):
                cell = cells.get(f"{pid}-{m}")
                atts = attachments.get(f"{pid}-{m}", [])
                amt = (cell or {}).get("amount")
                if amt:
                    total += amt
                    s = f"{amt:,.0f}"
                    if atts:
                        s += f" 📎×{len(atts)}"
                    row_vals.append(s)
                elif atts:
                    row_vals.append(f"📎×{len(atts)}")
                else:
                    row_vals.append("")
            row_vals.append(f"{total:,.0f}" if total else "")
            data.append(row_vals)
        df = pd.DataFrame(data, columns=col_names)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # —— 合同金额两行（暂估 / 结算 + 到账）+ 合同清单 + 就地人工修改（默认折叠，避免页面冗余）——
        with st.expander("💴 合同金额（年度口径，可人工修改；修改后 override 优先于自动值）", expanded=False):
            st.caption("逐项目展示合同暂估额 / 结算额 / 到账额，点「修改」就地编辑；默认折叠以免页面过长。")
            for r in rows:
                pid = r["project_id"]
                ca = contract_amounts.get(str(pid), {})
                est = ca.get("estimate", 0); est_ov = ca.get("estimate_override", False)
                stl = ca.get("settlement", 0); stl_ov = ca.get("settlement_override", False)
                inc = ca.get("income", 0); inc_ov = ca.get("income_override", False)
                mark_est = "　*（已人工修改）*" if est_ov else ""
                mark_stl = "　*（已人工修改）*" if stl_ov else ""
                mark_inc = "　*（已人工修改）*" if inc_ov else ""
                with st.container(border=True):
                    st.markdown(f"**{r['project_name']}**")
                    r1c1, r1c2 = st.columns([4, 1])
                    r1c1.markdown(f"合同暂估额：{fmt_money(est)}{mark_est}")
                    if r1c2.button("✏️ 修改暂估", key=f"ov_est_{pid}_{year}", use_container_width=True):
                        st.session_state["override_edit"] = {"project_id": pid, "year": year, "kind": "estimate", "label": "合同暂估额"}
                    r2c1, r2c2, r2c3 = st.columns([4, 1, 1])
                    r2c1.markdown(f"实际入账额：合同结算金额 {fmt_money(stl)}{mark_stl} ｜ 实际到账流水 {fmt_money(inc)}{mark_inc}")
                    if r2c2.button("✏️ 修改结算", key=f"ov_stl_{pid}_{year}", use_container_width=True):
                        st.session_state["override_edit"] = {"project_id": pid, "year": year, "kind": "settlement", "label": "合同结算金额"}
                    if r2c3.button("✏️ 修改到账", key=f"ov_inc_{pid}_{year}", use_container_width=True):
                        st.session_state["override_edit"] = {"project_id": pid, "year": year, "kind": "income", "label": "实际到账流水"}

                    # 就地渲染编辑表单（点击「修改」后立即出现在本项目卡片内）
                    ov_edit = st.session_state.get("override_edit")
                    if ov_edit and ov_edit.get("project_id") == pid and ov_edit.get("year") == year:
                        _cur = {"estimate": est, "settlement": stl, "income": inc}.get(ov_edit["kind"], 0.0)
                        _inp_key = f"ov_input_{ov_edit['kind']}_{pid}_{year}"
                        with st.form(f"override_form_{ov_edit['kind']}_{pid}_{year}"):
                            st.markdown(f"**人工修改：{ov_edit['label']}**（当前自动值 {fmt_money(_cur)}）")
                            new_val = st.number_input("金额（元）", min_value=0.0, step=1000.0, format="%.2f",
                                                      value=float(_cur or 0.0), key=_inp_key)
                            oc1, oc2, oc3 = st.columns([1, 1, 1])
                            save_ov = oc1.form_submit_button("💾 保存")
                            reset_ov = oc2.form_submit_button("↩️ 恢复自动值")
                            cancel_ov = oc3.form_submit_button("取消")
                        if save_ov:
                            r = api_save_contract_amount_override({"project_id": pid, "year": year,
                                                                   "kind": ov_edit["kind"], "amount": new_val})
                            if r and r.status_code == 200:
                                st.session_state.pop("override_edit", None)
                                st.session_state.pop(_inp_key, None)
                                st.rerun()
                            elif r is not None:
                                st.error(r.text)
                        if reset_ov:
                            r = api_save_contract_amount_override({"project_id": pid, "year": year,
                                                                   "kind": ov_edit["kind"], "amount": None})
                            if r and r.status_code == 200:
                                st.session_state.pop("override_edit", None)
                                st.session_state.pop(_inp_key, None)
                                st.rerun()
                            elif r is not None:
                                st.error(r.text)
                        if cancel_ov:
                            st.session_state.pop("override_edit", None)
                            st.session_state.pop(_inp_key, None)
                            st.rerun()

                    clist = ca.get("contracts", [])
                    with st.expander(f"合同清单（{len(clist)} 份）"):
                        if not clist:
                            st.caption("该项目暂无关联合同")
                        else:
                            crows = [{
                                "合同名称": _contract_display_name(cc),
                                "合同编号": cc.get("contract_no") or "",
                                "含税金额": fmt_money(cc.get("amount_incl_tax")),
                                "签订日期": cc.get("sign_date") or "",
                                "本年暂估": fmt_money(cc.get("estimate")),
                                "本年结算": fmt_money(cc.get("settlement")),
                            } for cc in clist]
                            st.dataframe(pd.DataFrame(crows), use_container_width=True, hide_index=True)

    st.divider()

    # —— 单元格录入 / 编辑 ——
    if rows:
        st.subheader("✏️ 单元格录入 / 编辑")
        st.caption("选择项目与月份，录入该月投入金额、经费科目、发生日期，并上传/下载该月支撑材料附件")
        row_map = {r["project_id"]: r for r in rows}
        e1, e2 = st.columns(2)
        sel_pid = e1.selectbox("项目", [r["project_id"] for r in rows],
                               format_func=lambda pid: row_map[pid]["project_name"], key="fg_sel_proj")
        sel_month = e2.selectbox("月份", list(range(1, 13)), format_func=lambda m: f"{m} 月", key="fg_sel_month")

        cell = cells.get(f"{sel_pid}-{sel_month}")
        atts = attachments.get(f"{sel_pid}-{sel_month}", [])

        with st.container(border=True):
            with st.form("funding_cell_form"):
                fc1, fc2 = st.columns(2)
                amt = fc1.number_input("投入金额（元）", min_value=0.0, step=1000.0,
                                       value=float((cell or {}).get("amount") or 0.0), format="%.2f")
                fund_date = fc2.text_input("经费发生日期", value=(cell or {}).get("fund_date") or "", placeholder="如：2026-03-15")
                item_name = st.text_input("经费科目 / 名称", value=(cell or {}).get("item_name") or "", placeholder="如：省拨经费、设备费、劳务费")
                save_cell = st.form_submit_button("💾 保存该单元格", use_container_width=True)
            if save_cell:
                r = api_save_funding_cell({
                    "year": year, "project_id": sel_pid, "month": sel_month,
                    "amount": amt, "item_name": item_name, "fund_date": fund_date,
                })
                if r and r.status_code == 200:
                    st.success("已保存")
                    st.rerun()
                elif r is not None:
                    st.error(r.text)

            st.markdown("**📎 支撑材料附件**（精确关联「项目 + 月份」）")
            if atts:
                for a in atts:
                    ac1, ac2 = st.columns([5, 1])
                    tok = st.session_state.get("token", "")
                    ac1.markdown(f"📄 `{a['file_name']}`　[⬇️ 下载]({API_BASE}{a['download_url']}?token={tok})")
                    if ac2.button("🗑️", key=f"fg_att_del_{a['id']}", use_container_width=True):
                        api_delete_funding_attachment(a["id"])
                        st.rerun()
            else:
                st.caption("（该单元格暂无附件）")
            up_files = st.file_uploader("上传附件（可多选）", key=f"fg_att_up_{sel_pid}_{sel_month}", accept_multiple_files=True)
            if st.button("⬆️ 上传附件", key="fg_att_up_btn", use_container_width=True):
                if not up_files:
                    st.warning("请先选择要上传的文件")
                else:
                    r = api_upload_funding_attachments(year, sel_pid, sel_month, up_files)
                    if r and r.status_code == 200:
                        d = r.json()
                        st.success(f"已上传 {len(d.get('uploaded', []))} 个附件")
                        st.rerun()
                    elif r is not None:
                        st.error(r.text)

        st.divider()

        # —— 行管理（删除 / 调整顺序）——
        st.subheader("🗂️ 项目行管理（删除 / 调整顺序）")
        for i, r in enumerate(rows):
            c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
            c1.markdown(f"**{r['project_name']}**")
            if c2.button("⬆️ 上移", key=f"fg_up_{r['id']}", disabled=(i == 0), use_container_width=True):
                api_move_funding_row(r["id"], "up")
                st.rerun()
            if c3.button("⬇️ 下移", key=f"fg_down_{r['id']}", disabled=(i == len(rows) - 1), use_container_width=True):
                api_move_funding_row(r["id"], "down")
                st.rerun()
            if c4.button("🗑️ 删除", key=f"fg_del_{r['id']}", use_container_width=True):
                st.session_state["fg_confirm_del_row"] = r["id"]

        if st.session_state.get("fg_confirm_del_row"):
            del_pid = st.session_state["fg_confirm_del_row"]
            del_row = next((r for r in rows if r["id"] == del_pid), None)
            del_name = del_row["project_name"] if del_row else f"#{del_pid}"
            st.warning(f"⚠️ 确定删除项目「{del_name}」这一行吗？该行所有月份的经费数据与附件都会一并删除，且无法恢复！")
            d1, d2 = st.columns([1, 1])
            if d1.button("✅ 确认删除", key="fg_del_confirm_yes"):
                api_delete_funding_row(del_pid)
                st.session_state.pop("fg_confirm_del_row", None)
                st.rerun()
            if d2.button("❌ 取消", key="fg_del_confirm_no"):
                st.session_state.pop("fg_confirm_del_row", None)
                st.rerun()

    st.divider()

    # ============ 新增：经费智能提取 / 比对 / 预警 ============
    with st.expander("⚙️ 经费口径配置（最终填充金额 / 超额基准 / 延期基准 / 容忍比例）", expanded=False):
        cfg = api_get_config()
        with st.form("fund_cfg_form"):
            cc1, cc2 = st.columns(2)
            basis_opts = {"发票额（默认）": "invoice", "暂估额": "estimate", "三列并存": "both"}
            cur_basis = cfg.get("final_amount_basis", "invoice")
            new_basis = cc1.selectbox(
                "最终填充金额口径", list(basis_opts.keys()),
                index=list(basis_opts.values()).index(cur_basis) if cur_basis in basis_opts.values() else 0,
            )
            ob_opts = {"合同含税金额（默认）": "contract_incl_tax", "项目预算总额": "project_budget"}
            cur_ob = cfg.get("overrun_basis", "contract_incl_tax")
            new_ob = cc2.selectbox(
                "超额判定基准", list(ob_opts.keys()),
                index=list(ob_opts.values()).index(cur_ob) if cur_ob in ob_opts.values() else 0,
            )
            tol = st.number_input("超额容忍比例（如 0.05 表示超 5% 才报警）", min_value=0.0, max_value=1.0,
                                  step=0.01, value=float(cfg.get("overrun_tolerance", "0.0") or 0))
            submit_cfg = st.form_submit_button("💾 保存口径配置")
        if submit_cfg:
            r = api_update_config({
                "final_amount_basis": basis_opts[new_basis],
                "overrun_basis": ob_opts[new_ob],
                "overrun_tolerance": str(tol),
            })
            if r and r.status_code == 200:
                st.success("配置已保存")
                st.rerun()
            elif r is not None:
                st.error(r.text)

    st.divider()

    # —— 经费智能比对（选项目带出合同金额/日期 + 财务资料 + 超额预警）——
    st.subheader("📑 经费智能比对")
    st.caption("选项目自动带出合同金额与日期；上传暂估单/结算单/发票自动提取金额税额日期，发票覆盖暂估结算，累计实际额 vs 合同含税额自动比对")

    _projs_all = api_list_projects()
    _proj_filter = {"（全部项目）": None}
    _proj_filter.update({f"{p['name']}（ID:{p['id']}）": p["id"] for p in _projs_all})
    # 跳转预选：从项目管理页跳转时按 current_project_id 预选
    _f_cpid = st.session_state.get("current_project_id")
    if _f_cpid is not None and st.session_state.get("_fund_filter_for") != _f_cpid:
        _target = next((lbl for lbl, pid in _proj_filter.items() if pid == _f_cpid), "（全部项目）")
        st.session_state["fund_cmp_proj"] = _target
        st.session_state["_fund_filter_for"] = _f_cpid
    sel_pf = st.selectbox("选择项目", list(_proj_filter.keys()), key="fund_cmp_proj")
    cmp_pid = _proj_filter[sel_pf]

    cmp = api_funding_compare(cmp_pid)
    if cmp:
        contracts = cmp.get("contracts", [])
        if not contracts:
            st.info("该项目暂无关联合同。请先在「合同管理」上传合同并关联项目。")
        else:
            tot = cmp.get("totals", {})
            t1, t2, t3, t4 = st.columns(4)
            t1.metric("合同含税总额", f"¥{tot.get('contract_incl_tax', 0):,.0f}")
            t2.metric("暂估合计", f"¥{tot.get('estimate', 0):,.0f}")
            t3.metric("结算合计", f"¥{tot.get('settlement', 0):,.0f}")
            t4.metric("发票合计", f"¥{tot.get('invoice', 0):,.0f}")

            projs = cmp.get("projects", [])
            if projs:
                import pandas as pd
                prows = []
                for p in projs:
                    prows.append({
                        "项目": p["project_name"], "合同数": p["contract_count"],
                        "合同含税额": f"¥{p['contract_incl_tax']:,.0f}",
                        "实际发生额": f"¥{p['effective']:,.0f}",
                        "差异": f"¥{p['diff']:,.0f}",
                        "进度": (f"{p['progress_pct']}%" if p['progress_pct'] is not None else "—"),
                        "超额": "🔴 是" if p["overrun"] else "否",
                    })
                st.markdown("**按项目汇总**")
                st.dataframe(pd.DataFrame(prows), use_container_width=True, hide_index=True)

            crows = []
            for c in contracts:
                crows.append({
                    "合同": _contract_display_name(c),
                    "乙方/合作方": c["party_b"] or "",
                    "含税金额": c["amount_incl_tax"],
                    "暂估": c["estimate_total"], "结算": c["settlement_total"], "发票": c["invoice_total"],
                    "最终金额": c["effective_total"] if c["effective_total"] is not None else "—",
                    "差异": c["diff_vs_contract"] if c["diff_vs_contract"] is not None else "—",
                    "签订日期": c["sign_date"], "到期日": c["due_date"],
                })
            st.markdown("**按合同明细**")
            st.dataframe(pd.DataFrame(crows), use_container_width=True, hide_index=True)

            for a in cmp.get("alerts", []):
                if a.get("category") == "超额":
                    st.error(f"🔴 {a['title']}：{a['detail']}")

    st.divider()

    # —— 自动归集的财务单据（文件库中识别出的发票/结算单/暂估单/估算单等）——
    st.subheader("📎 自动归集的财务单据")
    st.caption("文件管理上传时自动识别归集的财务单据（发票 / 结算单 / 暂估单 / 估算单等），此处汇总引用，可一键下载")
    fin_files = api_financial_files()
    if fin_files:
        fin_items = fin_files.get("items", [])
        by_type = fin_files.get("by_type", {})
        if fin_items:
            m1, m2 = st.columns(2)
            m1.metric("财务单据总数", len(fin_items))
            m2.metric("金额合计", f"¥{fin_files.get('total_amount', 0):,.2f}")
            if by_type:
                st.caption("　".join(f"{k}：¥{v:,.2f}" for k, v in by_type.items()))
            _fin_rows = [{
                "项目": it.get("project_name") or "",
                "类型": it.get("doc_type") or "其他财务单据",
                "金额": it.get("amount") or 0,
                "发生日期": it.get("doc_date") or "",
                "文件": it.get("original_name") or "",
            } for it in fin_items]
            st.dataframe(pd.DataFrame(_fin_rows), use_container_width=True, hide_index=True)
        else:
            st.info("文件库中暂未识别到财务单据。上传发票 / 结算单 / 暂估单 / 估算单等文件后会自动归集到这里。")
    else:
        st.info("文件库中暂未识别到财务单据。")

    # —— 财务资料上传 / 校对 ——
    st.subheader("🧾 财务资料（暂估单 / 结算单 / 发票）")
    st.caption("1 合同 → 多张暂估/结算/发票；一张发票只对应一个合同；识别结果须人工校对确认")
    contracts_all = api_list_contracts()
    if not contracts_all:
        st.info("暂无合同，请先在「合同管理」上传合同。")
    else:
        with st.container(border=True):
            upload_mode = st.radio("上传方式", ["按项目上传（推荐）", "按合同上传"], horizontal=True, key="fin_upload_mode")
            if upload_mode == "按项目上传（推荐）":
                _projs_for_fin = api_list_projects()
                _fin_proj_opts = {f"{p['name']}（ID:{p['id']}）": p["id"] for p in _projs_for_fin}
                if not _fin_proj_opts:
                    st.warning("暂无项目，请先创建项目。")
                else:
                    sel_proj_label = st.selectbox("选择项目", list(_fin_proj_opts.keys()), key="fin_proj_upload")
                    sel_proj_id = _fin_proj_opts[sel_proj_label]
                    proj_contracts = [c for c in contracts_all if c.get("project_id") == sel_proj_id]
                    if not proj_contracts:
                        st.warning("该项目暂无关联合同，请先在「合同管理」关联项目。")
                    else:
                        ct_labels = {_contract_display_label(c, with_project=False): c["id"] for c in proj_contracts}
                        default_labels = list(ct_labels.keys())
                        sel_cts = st.multiselect("关联合同（默认全选）", list(ct_labels.keys()), default=default_labels, key="fin_proj_cts")
                        sel_dt = st.selectbox("单据类型", ["发票", "结算单", "暂估单"], key="fin_proj_dt")
                        up_files = st.file_uploader("选择单据文件（可多选）", accept_multiple_files=True, key="fin_proj_uploader")
                        if st.button("⬆️ 上传并解析", key="fin_proj_up_btn", use_container_width=True):
                            if not up_files:
                                st.warning("请先选择要上传的单据文件")
                            elif not sel_cts:
                                st.warning("请至少选择一个关联合同")
                            else:
                                selected_ct_ids = [ct_labels[l] for l in sel_cts]
                                # 文件名含合同名/编号关键字自动匹配；未命中归入多选第一个合同
                                groups = {}
                                for f in up_files:
                                    matched_id = None
                                    for c in proj_contracts:
                                        if c["id"] not in selected_ct_ids:
                                            continue
                                        if (c.get("contract_name") and c["contract_name"] in f.name) or \
                                           (c.get("contract_no") and c["contract_no"] in f.name):
                                            matched_id = c["id"]
                                            break
                                    if matched_id is None:
                                        matched_id = selected_ct_ids[0]
                                    groups.setdefault(matched_id, []).append(f)
                                with st.spinner("正在上传并解析财务单据……"):
                                    ok_all, err_all = 0, []
                                    for cid, gfiles in groups.items():
                                        r = api_upload_financial_docs(cid, sel_dt, sel_proj_id, gfiles)
                                        if r and r.status_code == 200:
                                            d = r.json()
                                            ok_all += len(d.get("uploaded", []))
                                            err_all += [e.get("filename", "") for e in d.get("errors", [])]
                                        elif r is not None:
                                            err_all.append(r.text)
                                st.success(f"已上传 {ok_all} 份，正在后台提取金额/税额/日期")
                                if err_all:
                                    st.warning(f"部分失败：{'；'.join(err_all)}")
                                st.rerun()
            else:
                fu1, fu2, fu3 = st.columns(3)
                ct_opts = {_contract_display_label(c, with_project=True): c["id"] for c in contracts_all}
                sel_ct = fu1.selectbox("关联合同", list(ct_opts.keys()), key="fin_doc_ct")
                sel_dt = fu2.selectbox("单据类型", ["发票", "结算单", "暂估单"], key="fin_doc_type")
                up_files = fu3.file_uploader("选择单据文件（可多选）", accept_multiple_files=True, key="fin_doc_uploader")
                if st.button("⬆️ 上传并解析", key="fin_doc_up_btn", use_container_width=True):
                    if not up_files:
                        st.warning("请先选择要上传的单据文件")
                    else:
                        with st.spinner("正在上传并解析财务单据……"):
                            r = api_upload_financial_docs(ct_opts[sel_ct], sel_dt, None, up_files)
                        if r and r.status_code == 200:
                            d = r.json()
                            st.success(f"已上传 {len(d.get('uploaded', []))} 份，正在后台提取金额/税额/日期")
                            st.rerun()
                        elif r is not None:
                            st.error(r.text)

        docs = api_list_financial_docs()
        if docs:
            st.markdown("**财务资料清单**（可人工校对确认）")
            for d in docs:
                status = "✅ 已确认" if d.get("confirmed") else "🕓 待确认"
                with st.expander(f"{status} {d['doc_type']} ｜ {d['original_name']} ｜ 金额 ¥{d.get('amount') or 0:,.2f}"):
                    if d.get("remark"):
                        st.caption(f"📝 {d['remark']}")
                    with st.form(f"fin_doc_form_{d['id']}"):
                        f1, f2, f3 = st.columns(3)
                        n_amt = f1.number_input("金额（价税合计）", value=float(d.get("amount") or 0), step=100.0, format="%.2f")
                        n_tax = f2.number_input("税额", value=float(d.get("tax_amount") or 0), step=10.0, format="%.2f")
                        n_date = f3.text_input("发生日期", value=d.get("doc_date") or "", placeholder="YYYY-MM-DD")
                        fs = st.form_submit_button("💾 保存校对")
                    if fs:
                        r = api_update_financial_doc(d["id"], {"amount": n_amt, "tax_amount": n_tax, "doc_date": n_date})
                        if r and r.status_code == 200:
                            st.success("已保存")
                            st.rerun()
                        elif r is not None:
                            st.error(r.text)
                    bc1, bc2 = st.columns(2)
                    if not d.get("confirmed"):
                        if bc1.button("✅ 确认生效", key=f"fin_doc_cf_{d['id']}"):
                            api_confirm_financial_doc(d["id"])
                            st.rerun()
                    if bc2.button("🗑️ 删除", key=f"fin_doc_del_{d['id']}"):
                        api_delete_financial_doc(d["id"])
                        st.rerun()

    st.divider()

    # —— 风险预警 ——
    alerts = api_funding_alerts()
    if alerts:
        al = alerts.get("alerts", [])
        with st.expander(f"⚠️ 风险预警（{len(al)} 条）", expanded=bool(al)):
            if not al:
                st.success("🎉 当前无风险预警")
            else:
                if st.button("🧹 批量清理已过期提醒", key="alert_dismiss_expired"):
                    r = api_dismiss_expired_alert()
                    if r and r.status_code == 200:
                        st.success(f"已清理 {r.json().get('dismissed_count', 0)} 条过期提醒")
                        st.rerun()
                    elif r is not None:
                        st.error(r.text)
                for a in al:
                    lvl = a.get("level", "info")
                    color = {"danger": "#e53935", "warning": "#f59e0b", "info": "#3b82f6"}.get(lvl, "#3b82f6")
                    icon = {"danger": "🔴", "warning": "🟡", "info": "🔵"}.get(lvl, "🔵")
                    acol1, acol2 = st.columns([9, 1])
                    acol1.markdown(
                        f'<div style="border-left:4px solid {color};padding:8px 12px;margin:6px 0;background:#f7f8fa;border-radius:4px;">'
                        f'{icon} <b>{html.escape(a.get("category", ""))}</b> ｜ {html.escape(a.get("title", ""))}<br>'
                        f'<span style="color:#5f6368;font-size:0.9em;">{html.escape(a.get("detail", ""))}</span></div>',
                        unsafe_allow_html=True,
                    )
                    if acol2.button("🔕 不再提醒", key=f"alert_dismiss_{a.get('alert_key', a.get('source_id', ''))}", help="关闭该条预警，后续不再显示"):
                        r = api_dismiss_alert(a.get("alert_key"), a.get("category"))
                        if r and r.status_code == 200:
                            st.rerun()
                        elif r is not None:
                            st.error(r.text)

    st.divider()

    # —— 旧经费收支明细（预算 / 到账 / 支出，折叠保留，数据大屏仍引用）——
    with st.expander("💼 经费收支明细（预算 / 到账 / 支出）", expanded=False):
        _projects = api_list_projects()
        _project_options = {f"{p['name']}（ID:{p['id']}）": p["id"] for p in _projects}
        _project_options["（平台级 / 不关联项目）"] = None

        with st.container(border=True):
            st.subheader("➕ 录入经费收支")
            with st.form("funding_form"):
                c1, c2 = st.columns(2)
                with c1:
                    proj_label = st.selectbox("所属项目", list(_project_options.keys()), key="fund_proj")
                    item_name = st.text_input("经费科目 / 名称", placeholder="如：省拨经费、设备费、劳务费")
                with c2:
                    fund_type = st.selectbox("类型", ["到账", "支出", "预算"], key="fund_type")
                    amount = st.number_input("金额（元）", min_value=0.0, step=1000.0, format="%.2f")
                fund_date = st.text_input("日期（可选）", placeholder="如：2026-09-27")
                remark = st.text_input("备注（可选）")
                submitted = st.form_submit_button("保存")
            if submitted:
                if not item_name.strip():
                    st.error("请填写经费科目")
                elif amount <= 0:
                    st.error("金额需大于 0")
                else:
                    resp = api_create_funding({
                        "project_id": _project_options[proj_label],
                        "item_name": item_name.strip(),
                        "fund_type": fund_type,
                        "amount": amount,
                        "fund_date": fund_date.strip() or None,
                        "remark": remark.strip() or None,
                    })
                    if resp is None:
                        pass
                    elif resp.status_code == 200:
                        st.success("已保存")
                        st.rerun()
                    else:
                        st.error(f"保存失败：{resp.text}")

        _stats = api_funding_stats()
        if _stats:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("记录数", _stats.get("total_count", 0))
            c2.metric("预算总额", f"¥{_stats.get('budget', 0):,.2f}")
            c3.metric("到账总额", f"¥{_stats.get('income', 0):,.2f}")
            c4.metric("支出总额", f"¥{_stats.get('expense', 0):,.2f}")
            c5.metric("结余", f"¥{_stats.get('balance', 0):,.2f}")

        _items = api_list_fundings()
        if _items:
            rows = []
            for f in _items:
                rows.append({
                    "ID": f["id"],
                    "项目": f.get("project_name") or "平台级",
                    "科目": f["item_name"],
                    "类型": f["fund_type"],
                    "金额": f["amount"],
                    "日期": f.get("fund_date") or "",
                    "备注": f.get("remark") or "",
                })
            _df = pd.DataFrame(rows)
            st.dataframe(_df, use_container_width=True, hide_index=True)

            st.subheader("🗑️ 删除经费收支记录")
            _id_map = {f"{f['id']}": f for f in _items}
            _label_map = {f"{f['id']}": f"#{f['id']}｜{f['item_name']}｜¥{f['amount']}" for f in _items}
            _sel = st.selectbox("选择要删除的记录", list(_id_map.keys()), format_func=lambda k: _label_map[k], key="fund_del_sel")
            if st.button("删除所选", key="fund_del_btn"):
                r = api_delete_funding(int(_sel))
                if r and r.status_code == 200:
                    st.success("已删除")
                    st.rerun()
                else:
                    st.error("删除失败")

# ============ 页面4.2：成果台账 ============
elif menu == "🏆 成果台账":
    st.header("🏆 成果台账")
    st.caption("登记专利、论文、软著、获奖、标准等成果，按类型/状态/项目统计")

    projects = api_list_projects()
    project_options = {f"{p['name']}（ID:{p['id']}）": p["id"] for p in projects}
    project_options["（平台级 / 不关联项目）"] = None

    cats = ["专利", "论文", "软件著作权", "获奖", "标准", "成果登记", "鉴定报告", "其他"]
    statuses = ["在研", "已授权", "已发表", "已登记", "已获奖", "已发布", "其他"]

    # —— 上传成果文件，自动识别 ——
    with st.container(border=True):
        st.subheader("📤 上传成果文件自动识别")
        st.caption("可一次多选上传专利证书、论文、软著登记证书、获奖证书等文件，系统自动识别名称、类型、状态、权利人、取得日期；识别结果可自由编辑修正。")
        up_proj_label = st.selectbox("关联项目", list(project_options.keys()), key="ach_up_proj")
        up_files = st.file_uploader("选择成果文件（可多选）", key="ach_up_file", accept_multiple_files=True)
        if st.button("上传并自动识别", key="ach_up_btn"):
            if not up_files:
                st.warning("请先选择要上传的成果文件")
            else:
                with st.spinner(f"正在上传 {len(up_files)} 个文件并后台识别……"):
                    resp = api_upload_achievements(up_files, project_options[up_proj_label])
                if resp is None:
                    pass
                elif resp.status_code == 200:
                    data = resp.json()
                    n_ok = len(data.get("uploaded", []))
                    n_err = len(data.get("errors", []))
                    if n_err:
                        err_names = "；".join(e.get("filename", "") for e in data["errors"])
                        st.warning(f"上传完成：成功 {n_ok} 个，失败 {n_err} 个（{err_names}）")
                    else:
                        st.success(f"已上传 {n_ok} 个文件，正在后台自动识别，稍后刷新即可查看并编辑识别结果")
                    st.rerun()
                else:
                    st.error(f"上传失败：{resp.text}")

    st.divider()

    with st.container(border=True):
        st.subheader("➕ 登记成果")
        with st.form("achievement_form"):
            c1, c2 = st.columns(2)
            with c1:
                proj_label = st.selectbox("所属项目", list(project_options.keys()), key="ach_proj")
                name = st.text_input("成果名称", placeholder="如：一种流域级调速系统协同决策方法")
                holder = st.text_input("权利人 / 作者（可选）")
            with c2:
                category = st.selectbox("类型", cats, key="ach_cat")
                status = st.selectbox("状态", statuses, key="ach_status")
                achieve_date = st.text_input("取得日期（可选）", placeholder="如：2026-09-27")
            remark = st.text_input("备注（可选）")
            manual_file = st.file_uploader("附件（可选，如证书扫描件/证明材料）", key="ach_manual_file")
            submitted = st.form_submit_button("保存")
        if submitted:
            if not name.strip():
                st.error("请填写成果名称")
            else:
                resp = api_create_achievement_manual({
                    "project_id": project_options[proj_label],
                    "name": name.strip(),
                    "category": category,
                    "status": status,
                    "holder": holder.strip() or None,
                    "achieve_date": achieve_date.strip() or None,
                    "remark": remark.strip() or None,
                }, file=manual_file)
                if resp is None:
                    pass
                elif resp.status_code == 200:
                    st.success("已登记" + ("（含附件）" if manual_file else ""))
                    st.rerun()
                else:
                    st.error(f"登记失败：{resp.text}")

    st.divider()

    stats = api_achievement_stats()
    if stats and stats.get("total_count", 0) > 0:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.metric("成果总数", stats.get("total_count", 0))
        with c2:
            by_category = stats.get("by_category", {})
            if by_category:
                st.markdown("**按类型分布（份）**")
                st.bar_chart(by_category)
        if stats.get("by_status"):
            st.markdown("**按状态分布（份）**")
            st.bar_chart(stats.get("by_status"))
        st.divider()

    st.subheader("📋 成果明细")
    items = api_list_achievements()
    if not items:
        st.info("暂无成果记录。")
    else:
        import pandas as pd
        src_map = {"auto": "🤖 自动识别", "manual": "✍️ 手动登记", "file": "📁 文件同步"}
        proc_map = {"done": "✅ 已完成", "processing": "⏳ 识别中", "error": "⚠️ 识别异常"}

        # —— 筛选栏：按关键词 / 类型 / 状态 / 项目 / 取得日期 搜索与筛选 ——
        with st.container(border=True):
            st.markdown("**🔍 搜索与筛选**")
            f1, f2 = st.columns([2, 1])
            with f1:
                kw = st.text_input("🔍 关键词搜索", placeholder="成果名称 / 权利人 / 备注 / 关联文件", key="ach_filter_kw")
            with f2:
                fd1, fd2 = st.columns(2)
                with fd1:
                    date_from = st.text_input("取得日期（起）", placeholder="如 2026-01-01", key="ach_filter_date_from")
                with fd2:
                    date_to = st.text_input("取得日期（止）", placeholder="如 2026-12-31", key="ach_filter_date_to")
            f3, f4, f5 = st.columns(3)
            with f3:
                f_cats = st.multiselect("类型", cats, default=[], key="ach_filter_cats")
            with f4:
                f_statuses = st.multiselect("状态", statuses, default=[], key="ach_filter_statuses")
            with f5:
                f_proj = st.selectbox("所属项目", ["全部项目"] + list(project_options.keys()), key="ach_filter_proj")
            if st.button("🔄 重置", key="ach_filter_reset"):
                for k in ("ach_filter_kw", "ach_filter_date_from", "ach_filter_date_to",
                          "ach_filter_cats", "ach_filter_statuses", "ach_filter_proj"):
                    st.session_state.pop(k, None)
                st.rerun()

        # 前端过滤（数据量小，直接 Python 过滤；仅作用于「成果明细」表格展示）
        kw_l = (kw or "").strip().lower()
        date_from_s = (date_from or "").strip()
        date_to_s = (date_to or "").strip()
        filtered = []
        for a in items:
            if kw_l:
                hay = " ".join([
                    a.get("name") or "",
                    a.get("holder") or "",
                    a.get("remark") or "",
                    a.get("file_name") or "",
                ]).lower()
                if kw_l not in hay:
                    continue
            if f_cats and (a.get("category") or "") not in f_cats:
                continue
            if f_statuses and (a.get("status") or "") not in f_statuses:
                continue
            if f_proj != "全部项目" and (a.get("project_id") or None) != project_options[f_proj]:
                continue
            d = a.get("achieve_date") or ""
            if date_from_s and d < date_from_s:
                continue
            if date_to_s and d > date_to_s:
                continue
            filtered.append(a)

        st.caption(f"筛选结果 {len(filtered)} / 共 {len(items)} 条")

        rows = []
        for a in filtered:
            rows.append({
                "ID": a["id"],
                "项目": a.get("project_name") or "平台级",
                "成果名称": a["name"] or "（待识别…）",
                "类型": a.get("category") or "",
                "状态": a.get("status") or "",
                "权利人/作者": a.get("holder") or "",
                "取得日期": a.get("achieve_date") or "",
                "来源": src_map.get(a.get("source"), a.get("source") or "手动"),
                "识别状态": proc_map.get(a.get("processing_status"), a.get("processing_status") or "已完成"),
                "关联文件": a.get("file_name") or "",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        id_map = {f"{a['id']}": a for a in items}
        label_map = {f"{a['id']}": f"#{a['id']}｜{a['name'] or '（待识别）'}｜{a['category'] or '未知类型'}" for a in items}
        proj_keys = list(project_options.keys())

        # —— 编辑成果（自由修正识别结果）——
        st.subheader("✏️ 编辑成果")
        edit_sel = st.selectbox("选择要编辑的成果", list(id_map.keys()),
                                format_func=lambda k: label_map[k], key="ach_edit_sel")
        cur = id_map[edit_sel]
        if cur.get("download_url"):
            tok = st.session_state.get("token", "")
            st.markdown(f"📎 源文件：`{cur.get('file_name') or '未命名'}`　[⬇️ 下载]({API_BASE}{cur['download_url']}?token={tok})")

        def _proj_index(pid):
            for i, k in enumerate(proj_keys):
                if project_options[k] == pid:
                    return i
            return len(proj_keys) - 1

        def _opt_index(opts, val):
            return opts.index(val) if val in opts else 0

        with st.form("achievement_edit_form"):
            c1, c2 = st.columns(2)
            with c1:
                e_proj_label = st.selectbox("所属项目", proj_keys, index=_proj_index(cur.get("project_id")), key="ach_edit_proj")
                e_name = st.text_input("成果名称", value=cur["name"] or "", key="ach_edit_name")
                e_holder = st.text_input("权利人 / 作者（可选）", value=cur.get("holder") or "", key="ach_edit_holder")
            with c2:
                e_category = st.selectbox("类型", cats, index=_opt_index(cats, cur.get("category")), key="ach_edit_cat")
                e_status = st.selectbox("状态", statuses, index=_opt_index(statuses, cur.get("status")), key="ach_edit_status")
                e_date = st.text_input("取得日期（可选）", value=cur.get("achieve_date") or "", key="ach_edit_date")
            e_remark = st.text_input("备注（可选）", value=cur.get("remark") or "", key="ach_edit_remark")
            save_edit = st.form_submit_button("保存修改")
        if save_edit:
            if not e_name.strip():
                st.error("请填写成果名称")
            else:
                resp = api_update_achievement(int(edit_sel), {
                    "project_id": project_options[e_proj_label],
                    "name": e_name.strip(),
                    "category": e_category,
                    "status": e_status,
                    "holder": e_holder.strip() or None,
                    "achieve_date": e_date.strip() or None,
                    "remark": e_remark.strip() or None,
                })
                if resp is None:
                    pass
                elif resp.status_code == 200:
                    st.success("已保存修改")
                    st.rerun()
                else:
                    st.error(f"保存失败：{resp.text}")

        st.divider()

        with st.expander("🗑️ 删除成果（框选批量）", expanded=False):
            st.caption("勾选要删除的成果，可一次删除多个；删除不可恢复，请谨慎操作")
            tb1, tb2 = st.columns(2)
            with tb1:
                if st.button("☑️ 全选", key="ach_sel_all", use_container_width=True):
                    for a in items:
                        st.session_state[f"ach_sel_{a['id']}"] = True
                    st.rerun()
            with tb2:
                if st.button("⬜ 清空选择", key="ach_sel_none", use_container_width=True):
                    for a in items:
                        st.session_state[f"ach_sel_{a['id']}"] = False
                    st.rerun()
            for a in items:
                src_label = src_map.get(a.get("source"), a.get("source") or "手动")
                st.checkbox(f"#{a['id']}｜{a['name'] or '（待识别）'}｜{a.get('category') or '未知类型'}｜{src_label}", key=f"ach_sel_{a['id']}")
            selected_ids = [a["id"] for a in items if st.session_state.get(f"ach_sel_{a['id']}", False)]
            st.markdown(f"已选 **{len(selected_ids)}** 项")
            confirm = st.checkbox("我确认删除以上勾选的成果（不可恢复）", key="ach_batch_del_confirm")
            if st.button(f"⚠️ 一键删除所选（{len(selected_ids)}）", key="ach_batch_del_btn", disabled=(not selected_ids or not confirm)):
                r = api_batch_delete_achievements(selected_ids)
                if r and r.status_code == 200:
                    d = r.json()
                    st.success(f"已删除 {d.get('count', 0)} 条成果")
                    for a in items:
                        st.session_state.pop(f"ach_sel_{a['id']}", None)
                    st.rerun()
                else:
                    st.error("删除失败")

    st.divider()

    # —— 查重去重 ——
    st.subheader("🔍 查重去重")
    st.caption("按「成果名称（优先）＋ 项目 ＋ 来源文件」识别重复成果；同名但分属不同项目/文件的记录不会误删，每组只保留最新一条")
    if st.button("🔍 扫描重复成果", key="ach_dup_scan"):
        dup = api_achievement_duplicates()
        if dup is None:
            pass
        else:
            st.session_state["ach_dup_result"] = dup
    dup_result = st.session_state.get("ach_dup_result")
    if dup_result is not None:
        groups = dup_result.get("groups", [])
        total_remove = dup_result.get("total_remove", 0)
        if not groups:
            st.success("未发现重复成果 ✅")
        else:
            st.warning(f"发现 {len(groups)} 组重复，共 {total_remove} 条冗余记录可删除")
            for g in groups:
                keep = g["keep"]
                with st.expander(f"重复组：{g['key']}（{g['count']} 条）"):
                    st.markdown(f"**✅ 保留（最新）**：`{keep.get('name') or keep.get('file_name') or '未命名'}`（ID #{keep['id']}｜{keep.get('created_at', '')}）")
                    for r in g["remove"]:
                        st.markdown(f"　🗑️ 删除：`{r.get('name') or r.get('file_name') or '未命名'}`（ID #{r['id']}｜{r.get('created_at', '')}）")
            confirm = st.checkbox("我确认删除以上重复记录（每组保留最新一条）", key="ach_dup_confirm")
            if st.button("⚠️ 一键去重", key="ach_dup_go", disabled=not confirm):
                res = api_achievement_deduplicate()
                if res is None:
                    pass
                else:
                    st.success(f"已删除 {res.get('count', 0)} 条重复记录")
                    st.session_state.pop("ach_dup_result", None)
                    st.rerun()

# ============ 页面5：全文搜索 ============

elif menu == "🔍 全文搜索":
    st.header("全文搜索")

    with st.form("search_form"):
        keyword = st.text_input("搜索关键词", placeholder="例如：疲劳寿命、实验方法……")
        submitted = st.form_submit_button("🔍 搜索")

    if submitted:
        kw = keyword.strip()
        if not kw:
            st.warning("请输入要搜索的关键词。")
        else:
            with st.spinner("正在搜索……"):
                resp = api_search(kw)
            if resp.status_code != 200:
                st.error(f"搜索失败：{resp.text}")
            else:
                results = resp.json()
                if not results:
                    st.info(f"没有找到包含「{kw}」的文件。")
                else:
                    st.success(f"共找到 {len(results)} 个匹配文件")
                    for r in results:
                        with st.container(border=True):
                            st.markdown(f"**📄 {r['original_name']}**")
                            st.caption(f"📁 项目：{r['project_name']}（编号：{r['project_code']}）")
                            st.markdown(_highlight_keyword(r["snippet"], keyword), unsafe_allow_html=True)
                            with st.expander("👁️ 查看完整内容"):
                                resp_c = api_get_content(r["file_id"])
                                if resp_c.status_code == 200:
                                    c = resp_c.json().get("content", "")
                                    st.text_area("内容", c, height=300, key=f"search_{r['file_id']}")
                                else:
                                    st.warning("获取内容失败，请检查后端是否运行。")
# ============ 页面6：智能问答 ============
elif menu == "🤖 智能问答":
    render_ask_page()

# ============ 页面7：跨项目分析 ============
elif menu == "🌐 跨项目分析":
    st.header("🌐 跨项目分析")
    st.caption("跨所有项目检索同类型文件（如「科技项目申请书」「专利成果」），做统计汇总，并可据此撰写报告")

    question = st.text_area(
        "请输入你的需求（支持“汇总所有项目的科技项目申请书”“统计各项目专利成果并写份报告”等）",
        key="cross_input",
        height=100,
        placeholder="例如：汇总所有项目的科技项目申请书，并按项目统计数量",
    )

    if st.button("🔍 跨项目统计/生成报告", key="cross_btn"):
        q = question.strip()
        if not q:
            st.warning("请先输入需求")
        else:
            with st.spinner("正在跨项目检索并生成结果，请稍候……"):
                resp = api_cross_project(q)
            if resp is None:
                pass
            elif resp.status_code != 200:
                st.error(f"跨项目分析失败：{resp.text}")
            else:
                data = resp.json()
                stats = data.get("stats") or {}
                st.markdown("### 📊 统计概览")
                c1, c2, c3 = st.columns(3)
                c1.metric("命中文件总数", stats.get("total", 0))
                c2.metric("涉及项目数", stats.get("project_count", 0))
                per = stats.get("per_project") or {}
                with c3:
                    st.write("**按项目分布**")
                    for pname, cnt in per.items():
                        st.write(f"- {pname}：{cnt} 份")

                st.markdown("### 📝 统计/报告结果")
                st.markdown(data.get("answer", "（无结果）"))

                items = data.get("items") or []
                if items:
                    with st.expander(f"📎 命中文件明细（{len(items)} 份）"):
                        for it in items:
                            cat = it.get("category", "") or "其他"
                            dtype = it.get("doc_type", "") or ""
                            tags = " ｜ ".join(t for t in [cat, dtype] if t)
                            line = f"- 📄 **{it.get('original_name','未命名')}**（{it.get('project_name','')}）"
                            if tags:
                                line += f" ｜ {tags}"
                            dl = it.get("download_url", "")
                            if dl:
                                tok = st.session_state.get("token", "")
                                line += f"　[⬇️ 下载]({API_BASE}{dl}?token={tok})"
                            st.markdown(line)
                            if it.get("summary"):
                                st.caption(f"　　摘要：{it['summary']}")

# ============ 页面8：审计日志 ============
elif menu == "🔐 审计日志":
    st.header("🔐 审计日志")
    st.caption("记录登录、增删改、下载、问答等关键操作，便于安全追溯")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        limit = st.selectbox("显示条数", [100, 200, 500, 1000], index=1)
    with c2:
        if st.button("🔄 刷新日志", use_container_width=True):
            st.rerun()
    with c3:
        if st.button("🗂️ 归档清理过期日志", use_container_width=True,
                     help="把超过保留期（默认90天）的日志导出到 audit_archive/ 并从数据库删除（日志本体仍保留在归档文件里）"):
            st.session_state["confirm_archive"] = True

    if st.session_state.get("confirm_archive"):
        st.warning("⚠️ 归档清理会把超过保留期的审计日志导出到文件、再从数据库删除（日志本体仍保留在归档文件里），确定执行吗？")
        ca, cb = st.columns([1, 1])
        if ca.button("✅ 确认归档", use_container_width=True):
            resp = api_audit_archive()
            st.session_state.pop("confirm_archive", None)
            if resp is None:
                pass
            elif resp.status_code == 200:
                d = resp.json()
                st.success(f"已归档清理 {d.get('archived', 0)} 条旧日志 → {d.get('archive_dir', '')}")
                st.rerun()
            else:
                st.error(f"归档失败：{resp.text}")
        if cb.button("❌ 取消", use_container_width=True):
            st.session_state.pop("confirm_archive", None)
            st.rerun()

    logs = api_audit_logs(limit=limit)
    if not logs:
        st.info("暂无审计日志。")
    else:
        import pandas as pd
        df = pd.DataFrame(logs)
        cols = ["ts", "username", "action", "detail", "ip"]
        df = df[[c for c in cols if c in df.columns]]
        df = df.rename(columns={
            "ts": "时间", "username": "操作人", "action": "操作",
            "detail": "详情", "ip": "来源 IP",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)

# ============ 页面9：系统备份 ============
elif menu == "💾 系统备份":
    st.header("💾 系统备份")
    st.caption("备份数据库 + 全部上传文件，磁盘上以 AES 加密存储（.zip.enc），系统每天自动备份一次，也可手动触发")

    if st.button("📦 立即备份", use_container_width=True):
        with st.spinner("正在备份数据库和文件，请稍候……"):
            resp = api_backup_create()
        if resp is None:
            pass
        elif resp.status_code == 200:
            d = resp.json()
            st.success(f"备份完成（已加密）：{d.get('file', '')}")
            st.rerun()
        else:
            st.error(f"备份失败：{resp.text}")

    st.divider()

    data = api_backup_list()
    if not data:
        st.info("暂无备份记录。")
    else:
        enc_note = "🔒 已加密" if data.get("encrypted") else ""
        st.markdown(f"**备份目录**：`{data.get('dir', '')}`　｜　**保留最近**：{data.get('keep', '')} 份　｜　{enc_note}")
        st.caption("下载时会自动解密为可用的 zip；磁盘上的 .zip.enc 为加密文件，密钥保存在 .env 的 BACKUP_KEY。")
        items = data.get("items", [])
        if not items:
            st.info("暂无备份文件。")
        else:
            for it in items:
                col_a, col_b, col_c = st.columns([5, 2, 2])
                size_mb = it.get("size", 0) / 1024 / 1024
                col_a.markdown(f"🔒 `{it['name']}`")
                col_b.markdown(f"{size_mb:.1f} MB")
                col_c.markdown(f"{it.get('created_at', '')}")
                with st.expander("⬇️ 下载此备份（自动解密为 zip）"):
                    resp_dl = requests.get(
                        f"{API_BASE}/backup/download",
                        params={"name": it["name"]},
                        timeout=600,
                    )
                    if resp_dl.status_code == 200:
                        dl_name = it["name"][:-4] if it["name"].endswith(".enc") else it["name"]
                        st.download_button(
                            "下载备份文件（解密后 zip）",
                            data=resp_dl.content,
                            file_name=dl_name,
                            mime="application/zip",
                            key=f"bk_{it['name']}",
                        )
                    else:
                        st.warning("下载失败")
                st.divider()

# ============ 页面10：提醒中心 ============
elif menu.startswith("✅ 待办"):
    st.header("✅ 待办模块")
    st.caption("统一汇总：节点待办、逾期付款、延期、质保金到期、超额、合同超期/临期等所有提醒")

    if st.button("🔄 刷新待办", key="todo_refresh_btn"):
        api_refresh_todos()
        st.rerun()

    data = api_list_todos()
    unhandled = data.get("unhandled", 0)
    todos = data.get("todos", [])
    c1, c2, c3 = st.columns(3)
    c1.metric("待办总数", len(todos))
    c2.metric("未处理", unhandled)
    c3.metric("已处理", len(todos) - unhandled)
    st.divider()

    if not todos:
        st.success("🎉 当前没有待办事项。")
    else:
        if unhandled:
            if st.button(f"✅ 一键标记全部已处理（{unhandled} 条）", key="todo_handle_all"):
                api_handle_all_todos()
                st.rerun()
        for t in todos:
            lvl = t.get("level", "info")
            color = {"danger": "#e53935", "warning": "#f59e0b", "info": "#3b82f6"}.get(lvl, "#3b82f6")
            icon = {"danger": "🔴", "warning": "🟡", "info": "🔵"}.get(lvl, "🔵")
            done = t.get("status") == "已处理"
            with st.container(border=True):
                tc1, tc2 = st.columns([8, 1])
                tc1.markdown(
                    f'<div style="border-left:4px solid {color};padding:4px 8px;">'
                    f'{icon} <b>{html.escape(t.get("category", ""))}</b> ｜ {html.escape(t.get("title", ""))}'
                    f'　<span style="color:#9aa0a6;font-size:0.85em;">[{t.get("status", "")}]</span><br>'
                    f'<span style="color:#5f6368;font-size:0.9em;">{html.escape(t.get("detail", ""))}</span></div>',
                    unsafe_allow_html=True,
                )
                if not done:
                    if tc2.button("✅ 已处理", key=f"todo_done_{t['id']}"):
                        api_handle_todo(t["id"])
                        st.rerun()
                else:
                    tc2.markdown(f"<span style='color:#16a34a'>已处理 {t.get('handled_at', '')}</span>", unsafe_allow_html=True)

# ============ 页面11：数据大屏 ============
elif menu == "📊 数据大屏":
    st.header("📊 数据大屏")
    st.caption("全平台宏观数据总览（合同总览 / 经费总览支持时段筛选）")

    d = api_dashboard()
    if not d:
        st.info("暂无数据。")
    else:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("项目数", d.get("project_count", 0))
        c2.metric("文件数", d.get("file_count", 0))
        c3.metric("合同数", d.get("contract_count", 0))
        c4.metric("成果数", d.get("achievement_count", 0))
        c5.metric("经费记录", d.get("funding_count", 0))
        c6.metric("合同总金额", f"¥{d.get('contract_total_amount', 0):,.0f}")

        st.divider()

        # 文件 / 成果宏观图表（保留，无时段筛选）
        col_l, col_r = st.columns(2)
        with col_l:
            fc = d.get("file_by_category", {})
            if fc:
                st.markdown("**文件分类分布**")
                st.bar_chart(fc)
            fs = d.get("file_by_stage", {})
            if fs:
                st.markdown("**文件阶段分布**")
                st.bar_chart(fs)
        with col_r:
            ac = d.get("achievement_by_category", {})
            if ac:
                st.markdown("**成果类型分布**")
                st.bar_chart(ac)
            pfc = d.get("project_file_count", {})
            if pfc:
                st.markdown("**各项目文件数**")
                st.bar_chart(pfc)

        st.divider()

        # ===== 需求 3/7：合同总览 + 经费总览 两个 tab（各带时段筛选） =====
        tab_contract, tab_funding = st.tabs(["📑 合同总览", "💰 经费总览"])

        with tab_contract:
            st.caption("按合同签订日期做时段筛选，展示合同 KPI 与多维图表")
            c_start, c_end = time_filter_widget("dash_contract")
            cstats = api_contract_stats(start=c_start, end=c_end, date_field="sign_date")
            if not cstats or cstats.get("total_count", 0) == 0:
                st.info("当前时段暂无合同数据。")
            else:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("合同总数", cstats.get("total_count", 0))
                k2.metric("合同总金额", fmt_money(cstats.get("total_amount", 0)))
                k3.metric("已超期", len(cstats.get("overdue", [])))
                k4.metric("即将到期(30天)", len(cstats.get("upcoming", [])))

                _bs = cstats.get("by_status", {})
                if _bs:
                    _dc1, _dc2 = st.columns([1, 1.4])
                    with _dc1:
                        st.markdown("**合同状态分布**")
                        st.markdown(_status_donut_svg(_bs), unsafe_allow_html=True)
                        _status_icons = {"正常": "✅", "即将到期": "⏰", "已超期": "🔴", "未识别": "❓"}
                        for _k, _v in _bs.items():
                            st.caption(f"{_status_icons.get(_k, '')} {_k}：{_v} 份")
                    with _dc2:
                        _bt = cstats.get("by_type", {})
                        if _bt:
                            st.markdown("**按合同类型（份数）**")
                            st.bar_chart(_bt)
                        _abt = cstats.get("amount_by_type", {})
                        if _abt:
                            st.markdown("**按合同类型（金额）**")
                            st.bar_chart(_abt)

                _bpa = cstats.get("by_party_a", {})
                if _bpa:
                    st.markdown("**按甲方（金额）**")
                    st.bar_chart(_bpa)

        with tab_funding:
            st.caption("按经费/财务单据发生日期做时段筛选，展示预算执行与付款计划")
            f_start, f_end = time_filter_widget("dash_funding")
            fstats = api_funding_stats(start=f_start, end=f_end)
            fbudget = api_funding_budget(start=f_start, end=f_end)

            if fstats:
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("预算", fmt_money(fstats.get("budget", 0)))
                b2.metric("到账", fmt_money(fstats.get("income", 0)))
                b3.metric("支出", fmt_money(fstats.get("expense", 0)))
                b4.metric("结余", fmt_money(fstats.get("balance", 0)))

            if fbudget:
                nm = fbudget.get("next_month", {})
                nq = fbudget.get("next_quarter", {})
                p1, p2 = st.columns(2)
                p1.metric(f"下月预计付款（{nm.get('start', '')} ~ {nm.get('end', '')}）", fmt_money(nm.get("total", 0)))
                p2.metric(f"下季度预计付款（{nq.get('start', '')} ~ {nq.get('end', '')}）", fmt_money(nq.get("total", 0)))

                _cc1, _cc2 = st.columns(2)
                with _cc1:
                    if fbudget.get("by_month"):
                        st.markdown("**按月发生额**")
                        st.bar_chart(fbudget["by_month"])
                    if fbudget.get("by_year"):
                        st.markdown("**按年发生额**")
                        st.bar_chart(fbudget["by_year"])
                with _cc2:
                    if fbudget.get("by_quarter"):
                        st.markdown("**按季度发生额**")
                        st.bar_chart(fbudget["by_quarter"])
                    if fbudget.get("by_project"):
                        st.markdown("**按项目执行额**")
                        st.bar_chart(fbudget["by_project"])
                if fbudget.get("by_party"):
                    st.markdown("**按合作方执行额**")
                    st.bar_chart(fbudget["by_party"])

