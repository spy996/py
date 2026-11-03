# -*- coding: utf-8 -*-
"""等保测评调研表 · 反向生成。

思路：以公司上一版调研表为模板，保留其格式与叙述性章节，
用台账数据刷新「5 类设备表」与「信息系统基本情况」，并在末尾追加「台账数据说明」。
"""
from __future__ import annotations

import io
import os
from copy import copy
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "assets", "net")

TEMPLATES = {
    "木里河公司": os.path.join(TEMPLATE_DIR, "木里河调研表-模板.xlsx"),
    "唐央公司": os.path.join(TEMPLATE_DIR, "唐央调研表-模板.xlsx"),
    "木里新能": os.path.join(TEMPLATE_DIR, "木里新能调研表-模板.xlsx"),
}

CATEGORY_SHEETS = {
    "网络设备": "网络设备",
    "安全设备": "安全设备",
    "服务器存储": "服务器存储设备",
    "终端设备": "终端设备",
    "其他设备": "其他设备",
}


def _norm(v) -> str:
    return str(v or "").replace(" ", "").replace("\n", "").replace("\r", "").strip()


def _find_sheet(wb, name: str):
    if name in wb.sheetnames:
        return wb[name]
    for s in wb.sheetnames:
        if _norm(name) == _norm(s):
            return wb[s]
    return None


def _find_header_row(ws, must=("序号", "设备名称"), max_scan=10):
    for r in range(1, min(ws.max_row, max_scan) + 1):
        vals = [_norm(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 25) + 1)]
        joined = "|".join(vals)
        if all(m in joined for m in must):
            return r
    return None


def _capture_note(ws, header_row):
    """返回 (说明文本, 说明起始行)。"""
    for r in range(header_row + 1, ws.max_row + 1):
        vals = [_norm(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 20) + 1)]
        joined = "".join(vals)
        if "说明" in joined and len(joined) > 8:
            return joined, r
    return "", None


def _clear_rows(ws, start_row, max_col=25):
    for r in range(start_row, ws.max_row + 1):
        for c in range(1, max_col + 1):
            ws.cell(r, c).value = None


def _unmerge_below(ws, header_row):
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row > header_row:
            try:
                ws.unmerge_cells(str(rng))
            except Exception:
                pass


def _copy_row_style(ws, src_row, dst_row, max_col=25):
    for c in range(1, max_col + 1):
        src = ws.cell(src_row, c)
        dst = ws.cell(dst_row, c)
        dst._style = copy(src._style)


def _device_value(h: str, d: dict, idx: int):
    h = _norm(h)
    if h == "序号":
        return idx
    if "所属系统" in h:
        return d.get("system_name") or ""
    if "设备名称" in h:
        return d.get("name") or ""
    if "是否虚拟" in h:
        return d.get("is_virtual") or "否"
    if "业务应用系统" in h:
        return d.get("biz_app") or ""
    if "数据库管理系统" in h:
        return d.get("db_version") or "/"
    if "中间件" in h:
        return d.get("middleware") or "/"
    if "操作系统" in h or "系统及版本" in h:
        return d.get("os_version") or "厂商定制"
    if "品牌及型号" in h or "生产厂家及型号" in h:
        return d.get("brand_model") or ""
    if "设备类别/用途" in h or h == "用途":
        return d.get("purpose") or ""
    if "重要程度" in h:
        return d.get("importance") or "重要"
    if "IP地址" in h:
        return d.get("mgmt_ip") or "-"
    if "所属网络区域" in h:
        return d.get("zone") or ""
    if "物理环境名称" in h:
        return d.get("room") or ""
    if "是否热备" in h:
        return d.get("is_hot_standby") or "否"
    if "安装杀毒" in h:
        return d.get("antivirus") or "/"
    if "备注" in h:
        q = d.get("quantity") or 1
        base = (d.get("remark") or "").strip()
        if q and q > 1:
            return (base + f"；共{q}台").strip("；")
        return base
    return ""


def _fill_device_sheet(ws, devices, system_names):
    header_row = _find_header_row(ws, ("序号", "所属系统", "设备名称"))
    if header_row is None:
        return 0
    max_col = min(ws.max_column, 25)
    header = [ws.cell(header_row, c).value for c in range(1, max_col + 1)]
    note_text, note_row = _capture_note(ws, header_row)
    _unmerge_below(ws, header_row)
    _clear_rows(ws, header_row + 1, max_col)

    template_row = header_row + 1
    r = header_row + 1
    for i, d in enumerate(devices, 1):
        if r > ws.max_row:
            ws.append([])
        _copy_row_style(ws, template_row, r, max_col)
        for c, h in enumerate(header, 1):
            if not h:
                continue
            val = _device_value(str(h), d, i)
            ws.cell(r, c).value = val
        r += 1

    # 说明写在数据之后（合并 B:末列）
    if note_text:
        note_line = r + 1
        cell = ws.cell(note_line, 2)
        cell.value = note_text
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        try:
            ws.merge_cells(start_row=note_line, start_column=2, end_row=note_line, end_column=max_col)
        except Exception:
            pass
        ws.row_dimensions[note_line].height = 60
    return len(devices)


def _fill_basic_info(ws, systems, scope_system_id=None):
    header_row = _find_header_row(ws, ("序号", "信息系统名称"), max_scan=8)
    if header_row is None:
        return 0
    max_col = min(ws.max_column, 20)
    note_text, note_row = _capture_note(ws, header_row)
    # 先保存原行的叙述性字段（描述/技术/应用/方案等），避免被清空
    old_rows = {}
    last = (note_row - 1) if note_row else ws.max_row
    for r in range(header_row + 1, last + 1):
        raw = ws.cell(r, 8).value
        if not raw or str(raw).startswith("="):
            raw = ws.cell(r, 3).value
        nm = _norm(raw)
        if nm:
            old_rows[nm] = [ws.cell(r, c).value for c in range(1, max_col + 1)]
    _unmerge_below(ws, header_row)
    _clear_rows(ws, header_row + 1, min(ws.max_column, 25))
    template_row = header_row + 1
    r = header_row + 1
    n = 0
    # B 序号 / C 名称 / F 集成厂家 / H 名称 / N 等级 / O 大区 /
    # P 备案 / Q 测评 / R 整改 / S 备案号 / T 扩展
    updated_cols = {2, 3, 6, 8, 14, 15, 16, 17, 18, 19, 20}
    for s in systems:
        if scope_system_id and s.get("id") != scope_system_id:
            continue
        if r > ws.max_row:
            ws.append([])
        _copy_row_style(ws, template_row, r, min(ws.max_column, 25))
        level = f"{s.get('level_s') or ''}{s.get('level_a') or ''}" or (s.get("level") or "")
        name = s.get("name") or ""
        vals = {
            2: n + 1, 3: name, 6: s.get("integrator") or "", 8: name,
            14: level, 15: s.get("deploy_zone") or "",
            16: "是", 17: "是", 18: "是", 19: s.get("filing_no") or "",
            20: s.get("extension") or "工业控制系统",
        }
        old = old_rows.get(_norm(name))
        if old:
            for c in range(1, max_col + 1):
                if c not in updated_cols and old[c - 1] not in (None, ""):
                    ws.cell(r, c).value = old[c - 1]
        else:
            if not ws.cell(r, 4).value:
                ws.cell(r, 4).value = "（台账未存系统描述，请补充）"
            if not ws.cell(r, 5).value:
                ws.cell(r, 5).value = "（台账未存功能描述，请补充）"
        for c, v in vals.items():
            ws.cell(r, c).value = v
        r += 1
        n += 1
    if note_text:
        note_line = r + 1
        cell = ws.cell(note_line, 2)
        cell.value = note_text
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        try:
            ws.merge_cells(start_row=note_line, start_column=2, end_row=note_line, end_column=max_col)
        except Exception:
            pass
    return n


def _append_note_sheet(wb, company, scope_label, systems, devices, refreshed, gaps):
    name = "台账数据说明"
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name)
    ws.append(["项目", "内容"])
    rows = [
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("单位", company),
        ("生成范围", scope_label),
        ("系统数量", len(systems)),
        ("系统清单", "；".join(s.get("name", "") for s in systems)),
        ("设备总数", len(devices)),
        ("设备分布", "；".join(f"{k} {sum(1 for d in devices if d.get('category') == k)} 台"
                              for k in CATEGORY_SHEETS)),
        ("已刷新工作表", "、".join(refreshed)),
        ("沿用模板、需人工核对的章节", "、".join(gaps)),
        ("使用说明", "本表由「网络通讯台账」按台账数据生成；设备表与信息系统基本情况已刷新，"
                    "叙述性章节保留模板原文，请在提交前核对并补充。"),
    ]
    for k, v in rows:
        ws.append([k, v])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 100
    for r in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")


def build_investigation(company: str, systems: list, devices: list,
                        scope_system_id: int = None) -> tuple[bytes, str]:
    """生成调研表，返回 (xlsx bytes, 文件名)。"""
    path = TEMPLATES.get(company)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"未找到 {company} 的调研表模板")
    wb = openpyxl.load_workbook(path)
    system_names = {s["id"]: s.get("name", "") for s in systems}
    refreshed = ["信息系统基本情况"]

    for cat, sheet_name in CATEGORY_SHEETS.items():
        ws = _find_sheet(wb, sheet_name)
        if ws is None:
            continue
        subset = [d for d in devices if d.get("category") == cat]
        _fill_device_sheet(ws, subset, system_names)
        refreshed.append(sheet_name)

    ws_basic = _find_sheet(wb, "信息系统基本情况")
    if ws_basic is not None:
        _fill_basic_info(ws_basic, systems, scope_system_id)

    gaps = ["单位基本情况", "安全及运维人员", "物理环境情况", "外联线路情况", "网络结构情况",
            "网络拓扑图", "网络架构描述", "系统管理软件平台", "业务应用系统平台", "数据资源",
            "密码产品", "管理类文档", "威胁情况调研", "前次测评情况", "安全服务", "新技术新应用"]
    scope_label = f"单个系统（ID {scope_system_id}）" if scope_system_id else "按公司"
    if scope_system_id:
        target = next((s for s in systems if s.get("id") == scope_system_id), None)
        scope_label = f"单个系统：{target.get('name', '') if target else scope_system_id}"
    _append_note_sheet(wb, company, scope_label, systems, devices, refreshed, gaps)

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    suffix = next((s.get("name", "") for s in systems if s.get("id") == scope_system_id), "") if scope_system_id else company
    safe = "".join(ch for ch in suffix if ch not in r'\/:*?"<>|')[:30] or company
    filename = f"等保调研表_{safe}_{datetime.now():%Y%m%d}.xlsx"
    return buf.getvalue(), filename
