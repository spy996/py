# -*- coding: utf-8 -*-
"""网络通讯台账 · 本地规则引擎（无 AI）。

职责：
1. 文件名 / 报告正文解析（等保测评报告元数据、整改清单）
2. MAC / IP 清洗与校验
3. 调研表（5 类设备）与 IP/MAC 统计表导入映射

所有解析均为确定性规则：正则 + 章节定位 + 表头映射，不调用任何大模型。
"""
from __future__ import annotations

import os
import re
import ipaddress
from datetime import date

WS_RE = re.compile(r"[\s\u3000\u00a0]+")
MAC_HEX_RE = re.compile(r"^[0-9A-F]{12}$")

# 站点关键词 -> 站点编码（长关键词优先）
SITE_KEYS = [
    ("集控中心", "JKS"), ("集控", "JKS"),
    ("上通坝", "STB"), ("卡基娃", "KJW"), ("俄公堡", "EGB"), ("立洲", "LZ"),
    ("大丰", "DF"), ("绒达", "RD"), ("当依", "DY"), ("汇康", "HK"),
    ("让白", "RB"), ("杜依", "RB"),
]

DEVICE_CATEGORIES = ["网络设备", "安全设备", "服务器存储", "终端设备", "其他设备"]

REPORT_DOC_TYPES = ["测评报告", "备案证明", "整改报告", "自查报告", "其他"]

LEVEL_MAP = {"三级": ("S3", "A3"), "二级": ("S2", "A2")}


# ---------------------------------------------------------------- 基础清洗
def norm(s) -> str:
    """去空白（含全角空格、换行），用于解析与比对。"""
    return WS_RE.sub("", str(s or ""))


def normalize_mac(raw: str):
    """MAC 标准化：大写、冒号分隔、12 位十六进制。

    返回 (mac, ok)。无法识别时返回 ("", False)，调用方保留原文到 mac_raw。
    """
    s = str(raw or "").strip()
    if not s:
        return "", False
    s = (s.replace("：", ":").replace("－", "-").replace("—", "-")
          .replace("–", "-").replace(".", ":").replace(" ", "").replace("\n", ""))
    s = s.replace("-", ":")
    hex_only = re.sub(r"[^0-9A-Fa-f]", "", s)
    if len(hex_only) == 12:
        return ":".join(hex_only[i:i + 2].upper() for i in range(0, 12, 2)), True
    return "", False


def is_valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(str(s or "").strip())
        return True
    except Exception:
        return False


def guess_site_code(text: str) -> str:
    t = str(text or "")
    for key, code in SITE_KEYS:
        if key in t:
            return code
    return ""


def guess_site_by_ip(ip: str) -> str:
    """按常见网段粗分：10.168.21.* 为成都本部，其余现场不细分。"""
    s = str(ip or "")
    if s.startswith("10.168.21."):
        return "JKS"
    return ""


# ---------------------------------------------------------------- 文件名解析
def report_filename_meta(filename: str) -> dict:
    """从文件名解析等保资料元数据。

    支持：
      NZSA202651-057-DB-ST-等保2.0测评报告-<单位>-<系统> - 盖章版.docx
      大丰风电场网络安全等级保护备案证明.pdf
      备案证明-卡基娃电站（22年6月）.jpg
    """
    base = os.path.basename(filename or "")
    stem = os.path.splitext(base)[0]
    meta = {
        "doc_type": "其他", "project_no": "", "unit": "", "system_name": "",
        "report_year": None, "confidence": 0, "file_ext": os.path.splitext(base)[1].lower(),
    }

    m = re.match(r"^(.+?)-等保2\.0测评报告-(.+?)-(.+?)\s*-?\s*盖章版$", stem)
    if m:
        meta.update(doc_type="测评报告", project_no=m.group(1).strip(),
                    unit=m.group(2).strip(), system_name=m.group(3).strip(),
                    confidence=60)
    else:
        # 备案证明 / 整改报告 / 自查报告
        if "备案证明" in stem or "备案表" in stem:
            meta["doc_type"] = "备案证明"
            meta["confidence"] = 40
        elif "整改" in stem:
            meta["doc_type"] = "整改报告"
            meta["confidence"] = 40
        elif "自查" in stem:
            meta["doc_type"] = "自查报告"
            meta["confidence"] = 40
        elif "测评报告" in stem or "等级测评" in stem:
            meta["doc_type"] = "测评报告"
            meta["confidence"] = 30
        # 从文件名里剥掉类型词，剩下的当系统名候选
        cand = re.sub(r"(网络安全等级保护|等级保护|等保2\.0|测评报告|备案证明|备案表|整改报告|自查报告|盖章版|电子版|扫描件)", "", stem)
        cand = re.sub(r"^[\s\-_—]+|[\s\-_—]+$", "", cand)
        meta["system_name"] = cand
        meta["confidence"] = meta["confidence"] + 10 if cand else meta["confidence"]

    ym = re.search(r"(20\d{2})", stem)
    if ym:
        meta["report_year"] = int(ym.group(1))
    return meta


# ---------------------------------------------------------------- docx 解析基础
def _docx_blocks(doc):
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def _table_rows(tb):
    rows = []
    for r in tb.rows:
        vals, prev = [], None
        for c in r.cells:
            t = norm(c.text)
            # 合并单元格会重复返回同一文本，去重
            if t == prev:
                continue
            vals.append(t)
            prev = t
        rows.append(vals)
    return rows


def _headings(blocks):
    from docx.text.paragraph import Paragraph
    out = []
    for i, b in enumerate(blocks):
        if not isinstance(b, Paragraph):
            continue
        t = norm(b.text)
        if not t or len(t) > 80:
            continue
        if re.match(r"^(\d+(\.\d+)*|附录[A-Z])", t):
            out.append((i, t))
    return out


def _section_text(blocks, heads, start_kw, stop_re, limit=200):
    idx = next((i for i, t in heads if t.startswith(start_kw)), None)
    if idx is None:
        return ""
    stop = len(blocks)
    for i, t in heads:
        if i > idx and re.match(stop_re, t):
            stop = i
            break
    from docx.text.paragraph import Paragraph
    parts = []
    for b in blocks[idx + 1:stop]:
        if isinstance(b, Paragraph):
            parts.append(norm(b.text))
            if len(parts) >= limit:
                break
    return "".join(parts)


def _tables_between(blocks, heads, start_kw, stop_re):
    from docx.table import Table
    idx = next((i for i, t in heads if t.startswith(start_kw)), None)
    if idx is None:
        return []
    stop = len(blocks)
    for i, t in heads:
        if i > idx and re.match(stop_re, t):
            stop = i
            break
    return [b for b in blocks[idx + 1:stop] if isinstance(b, Table)]


# ---------------------------------------------------------------- 报告元数据
def _parse_docx_meta(path: str) -> dict:
    from docx import Document
    doc = Document(path)
    blocks = list(_docx_blocks(doc))
    heads = _headings(blocks)
    meta = {}
    from docx.text.paragraph import Paragraph
    full = "".join(norm(b.text) for b in blocks if isinstance(b, Paragraph))

    # 2.1.1 定级结果后第一张表
    tbs = _tables_between(blocks, heads, "2.1.1", r"^2\.1\.2")
    if tbs:
        for row in _table_rows(tbs[0]):
            if len(row) >= 4 and "被测对象" not in row[0] and row[1] in ("二级", "三级"):
                meta["system_name"] = row[0]
                meta["level"] = row[1]
                ls = "S3" if row[2] == "三级" else ("S2" if row[2] == "二级" else "")
                la = "A3" if row[3] == "三级" else ("A2" if row[3] == "二级" else "")
                meta["level_s"], meta["level_a"] = ls, la
                break

    # 测评起止日期
    m = re.search(r"从(\d{4})年(\d{1,2})月(\d{1,2})日.*?至(\d{4})年(\d{1,2})月(\d{1,2})日", full)
    if m:
        meta["eval_start"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        meta["eval_end"] = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
        meta["report_year"] = int(m.group(4))

    # 测评机构
    m = re.search(r"委托(.{2,40}?)开展", full)
    if m:
        meta["agency"] = m.group(1)

    # 测评项分布（全文多处，取总项数最大者）
    cands = re.findall(
        r"共测评(\d+)项，?其中符合项(\d+)项，?部分符合项(\d+)项，?不符合项(\d+)项，?不适用项(\d+)项", full)
    if cands:
        best = max(cands, key=lambda x: int(x[0]))
        meta.update(item_total=int(best[0]), item_pass=int(best[1]), item_partial=int(best[2]),
                    item_fail=int(best[3]), item_na=int(best[4]))

    # 符合率 / 结论
    m = re.search(r"符合率为(\d+(?:\.\d+)?)%", full)
    if m:
        meta["pass_rate"] = float(m.group(1))
    m = re.search(r"等级测评结论判定为(.{2,6}?)[。；，]", full) or re.search(r"判定为(.{2,6}?)[。；，]", full)
    if m:
        meta["conclusion"] = m.group(1)
    else:
        m = re.search(r"(基本符合|不符合|符合)", full)
        if m:
            meta["conclusion"] = m.group(1)

    # 上次测评
    m = re.search(r"于(\d{4})年(\d{1,2})月开展，?综合得分为(\d+(?:\.\d+)?)，?等级测评结论为(.{1,4}?)[。，]", full)
    if m:
        meta["last_eval_date"] = f"{m.group(1)}-{int(m.group(2)):02d}"
        meta["last_score"] = float(m.group(3))
        meta["last_conclusion"] = m.group(4)

    # 重大风险隐患
    meta["major_risk"] = 0 if "不存在重大风险隐患" in full else (1 if "存在重大风险隐患" in full else 0)
    return meta


def _parse_pdf_meta(path: str) -> dict:
    import fitz
    doc = fitz.open(path)
    try:
        text = "".join(page.get_text() for page in doc)
    finally:
        doc.close()
    t = norm(text)
    meta = {}
    m = re.search(r"共测评(\d+)项，?其中符合项(\d+)项，?部分符合项(\d+)项，?不符合项(\d+)项，?不适用项(\d+)项", t)
    if m:
        meta.update(item_total=int(m.group(1)), item_pass=int(m.group(2)), item_partial=int(m.group(3)),
                    item_fail=int(m.group(4)), item_na=int(m.group(5)))
    m = re.search(r"符合率为(\d+(?:\.\d+)?)%", t)
    if m:
        meta["pass_rate"] = float(m.group(1))
    m = re.search(r"判定为(.{2,6}?)[。；，]", t)
    if m:
        meta["conclusion"] = m.group(1)
    m = re.search(r"被测对象名称.{0,20}?([\u4e00-\u9fa5A-Za-z0-9（）()]{4,40})(二级|三级)(二级|三级)(二级|三级)", t)
    if m:
        meta["system_name"] = m.group(1)
        meta["level"] = m.group(2)
    m = re.search(r"从(\d{4})年(\d{1,2})月(\d{1,2})日.*?至(\d{4})年(\d{1,2})月(\d{1,2})日", t)
    if m:
        meta["eval_start"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        meta["eval_end"] = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
        meta["report_year"] = int(m.group(4))
    meta["major_risk"] = 0 if "不存在重大风险隐患" in t else (1 if "存在重大风险隐患" in t else 0)
    return meta


def parse_report_meta(path: str, filename: str = "") -> dict:
    """解析报告文件（docx/pdf），返回台账字段 + 置信度。"""
    meta = report_filename_meta(filename or os.path.basename(path))
    ext = os.path.splitext(path)[1].lower()
    try:
        body = _parse_docx_meta(path) if ext == ".docx" else (_parse_pdf_meta(path) if ext == ".pdf" else {})
    except Exception as e:
        meta["parse_error"] = f"{type(e).__name__}: {e}"
        body = {}
    # 文件名优先的系统名，正文只补空
    for k, v in body.items():
        if v not in (None, "", 0, 0.0) and not meta.get(k):
            meta[k] = v
    # 置信度累加
    conf = int(meta.get("confidence") or 0)
    if meta.get("level"):
        conf += 10
    if meta.get("conclusion"):
        conf += 10
    if meta.get("pass_rate"):
        conf += 10
    if meta.get("system_name"):
        conf += 10
    meta["confidence"] = min(conf, 100)
    return meta


# ---------------------------------------------------------------- 整改清单
def extract_findings(docx_path: str) -> list:
    """从测评报告 docx 抽取整改清单。

    3.14 提供问题主体；5.1 提供风险等级；8.1 提供整改建议；附录 B 提供前次整改状态。
    """
    from docx import Document
    doc = Document(docx_path)
    blocks = list(_docx_blocks(doc))
    heads = _headings(blocks)

    def collect(start_kw, stop_re):
        out = []
        for tb in _tables_between(blocks, heads, start_kw, stop_re):
            rows = _table_rows(tb)
            if not rows:
                continue
            header = rows[0]
            # 跳过表头行（首列为"问题编号"/"序号"）
            body = rows[1:] if header and header[0] in ("问题编号", "序号") else rows
            cur = None
            for r in body:
                if not r:
                    continue
                if re.fullmatch(r"\d+", r[0] or ""):
                    cur = {"no": int(r[0]), "cells": list(r[1:])}
                    out.append(cur)
                elif cur is not None:
                    # 跨页续表：把非空单元格追加到上一条对应位置
                    for j, v in enumerate(r[1:] if len(r) > len(cur["cells"]) else r):
                        if not v:
                            continue
                        k = j if len(r) <= len(cur["cells"]) + 1 else j - 1
                        if 0 <= k < len(cur["cells"]):
                            if v not in cur["cells"][k]:
                                cur["cells"][k] = (cur["cells"][k] + v).strip()
        return out

    problems = collect("3.14", r"^4[^\d.]")
    risks = collect("5.1", r"^5\.2")
    fixes = collect("8.1", r"^8\.2")
    prev = collect("附录B", r"^附录C")

    risk_map = {}
    for p in risks:
        risk = next((c for c in reversed(p["cells"]) if c in ("高", "中", "低")), "")
        if risk:
            risk_map[p["no"]] = risk
    fix_map = {p["no"]: (p["cells"][-1] if p["cells"] else "") for p in fixes}
    prev_map = {}
    for p in prev:
        cells = p["cells"]
        desc = cells[0] if cells else ""
        status = next((c for c in cells if "已整改" in c or "未整改" in c), "")
        if desc:
            prev_map[desc[:30]] = "未整改" if "未整改" in status else ("已整改" if "已整改" in status else "")

    items = []
    for p in problems:
        c = p["cells"] + [""] * 6
        desc = c[0]
        if not desc:
            continue
        item = {
            "problem_no": p["no"],
            "description": desc,
            "target": c[1],
            "security_class": c[3] or c[2],
            "control_point": c[4],
            "requirement": c[5],
            "risk_level": risk_map.get(p["no"], ""),
            "suggestion": fix_map.get(p["no"], ""),
            "prev_status": "",
            "source": "report",
        }
        for k, st in prev_map.items():
            if k and (k in desc or desc[:20] in k):
                item["prev_status"] = st
                break
        items.append(item)
    return items


# ---------------------------------------------------------------- 调研表导入
_SHEET_CATEGORY = {
    "网络设备": "网络设备",
    "安全设备": "安全设备",
    "服务器存储设备": "服务器存储",
    "服务器/存储设备": "服务器存储",
    "服务器存储": "服务器存储",
    "终端设备": "终端设备",
    "其他设备": "其他设备",
}


def _cell_text(v):
    if v is None:
        return ""
    return str(v).replace("\n", " ").replace("\r", "").strip()


def parse_investigation(path: str) -> dict:
    """解析等保调研表：5 类设备 + 系统清单。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    devices, systems = [], []

    # 系统清单
    if "信息系统基本情况" in wb.sheetnames:
        ws = wb["信息系统基本情况"]
        rows = [[_cell_text(c.value) for c in r] for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 40), max_col=25)]
        for r in rows:
            if len(r) > 3 and r[1].isdigit() and r[2]:
                systems.append({
                    "name": r[2],
                    "level": r[13] if len(r) > 13 else "",
                    "deploy_zone": r[14] if len(r) > 14 else "",
                    "filing_no": r[18] if len(r) > 18 else "",
                })

    for ws in wb.worksheets:
        cat = _SHEET_CATEGORY.get(ws.title.strip())
        if not cat:
            continue
        rows = [[_cell_text(c.value) for c in r] for r in ws.iter_rows(max_col=min(ws.max_column, 30))]
        header_i = None
        for i, r in enumerate(rows[:12]):
            joined = "|".join(r)
            if "序号" in joined and "所属系统" in joined and "设备名称" in joined:
                header_i = i
                break
        if header_i is None:
            continue
        header = [h.replace(" ", "").replace("\n", "") for h in rows[header_i]]

        def col(*names):
            for n in names:
                for j, h in enumerate(header):
                    if n in h:
                        return j
            return None

        idx = {
            "system": col("所属系统"),
            "name": col("设备名称"),
            "virtual": col("是否虚拟"),
            "os": col("操作系统及版本", "系统及版本", "操作系统/控制软件"),
            "brand": col("品牌及型号", "生产厂家及型号"),
            "purpose": col("设备类别/用途", "用途"),
            "importance": col("重要程度"),
            "remark": col("备注"),
            "zone": col("所属网络区域"),
            "room": col("物理环境名称"),
            "ip": col("IP地址"),
            "hot": col("是否热备"),
            "biz_app": col("所属业务应用系统"),
            "db": col("数据库管理系统"),
            "middleware": col("中间件"),
            "antivirus": col("安装杀毒软件"),
        }
        last_system = ""
        for r in rows[header_i + 1:]:
            def g(k):
                j = idx.get(k)
                return r[j] if j is not None and j < len(r) else ""
            name = g("name")
            if not name or name in ("-", "无", "／", "/"):
                continue
            if "说明" in "".join(r[:3]):
                continue
            sysname = g("system") or last_system
            last_system = sysname
            remark = g("remark")
            qty = 1
            mq = re.search(r"(\d+)\s*台", remark)
            if mq:
                qty = max(1, int(mq.group(1)))
            devices.append({
                "category": cat,
                "system_name": sysname,
                "name": name,
                "is_virtual": g("virtual") or "否",
                "os_version": g("os"),
                "brand_model": g("brand"),
                "purpose": g("purpose"),
                "importance": g("importance") or "重要",
                "remark": remark,
                "zone": g("zone"),
                "room": g("room"),
                "mgmt_ip": g("ip"),
                "is_hot_standby": g("hot") or "否",
                "quantity": qty,
                "biz_app": g("biz_app"),
                "db_version": g("db"),
                "middleware": g("middleware"),
                "antivirus": g("antivirus"),
                "status": "在用",
            })
    wb.close()
    return {"systems": systems, "devices": devices}


# ---------------------------------------------------------------- IP/MAC 导入
def parse_ip_mac(path: str) -> list:
    """解析 IP/MAC 统计表（原表 7 列 / 整理后加列均兼容）。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = [[_cell_text(c.value) for c in r] for r in ws.iter_rows(max_col=min(ws.max_column, 15))]
    header_i = None
    for i, r in enumerate(rows[:15]):
        joined = "|".join(r).upper()
        if "IP" in joined and "MAC" in joined and "序号" in joined:
            header_i = i
            break
    if header_i is None:
        wb.close()
        return []
    header = [h.replace(" ", "") for h in rows[header_i]]

    def col(*names):
        for n in names:
            for j, h in enumerate(header):
                if n in h:
                    return j
        return None

    idx = {
        "dept": col("部门"), "person": col("姓名"), "dtype": col("电脑类型", "设备类型"),
        "location": col("使用地点", "地点"), "ip": col("IP地址"), "mac": col("MAC地址"),
        "wangjian": col("网鉴"), "remark": col("备注"),
    }
    out = []
    for r in rows[header_i + 1:]:
        def g(k):
            j = idx.get(k)
            return r[j] if j is not None and j < len(r) else ""
        ip_raw = g("ip").strip()
        mac_raw = g("mac").strip()
        if not ip_raw and not mac_raw:
            continue
        status = "在用"
        remark = g("remark")
        if "停止使用" in remark or "停用" in remark:
            status = "停用"
        if "未接网线" in ip_raw or "未接" in ip_raw:
            status = "未接网线"
            ip_raw = ""
        mac, ok = normalize_mac(mac_raw)
        if not ok and mac_raw:
            remark = (remark + f"；原MAC文本:{mac_raw}").strip("；")
        if "固定IP" in mac_raw:
            remark = (remark + "；MAC未登记(固定IP)").strip("；")
        out.append({
            "ip": ip_raw,
            "mac": mac,
            "mac_raw": mac_raw,
            "department": g("dept").replace(" ", ""),
            "person": g("person"),
            "device_type": g("dtype"),
            "location": g("location"),
            "status": status,
            "wangjian": g("wangjian"),
            "remark": remark,
            "site_code": guess_site_code(g("location") + g("dept")) or guess_site_by_ip(ip_raw),
            "source": "import",
        })
    wb.close()
    return out


# ---------------------------------------------------------------- 汇总工具
def quality_report(addresses: list) -> dict:
    """地址台账数据质量：重复 IP/MAC、格式异常、缺失字段。"""
    from collections import Counter
    ip_c = Counter(a.get("ip") for a in addresses if a.get("ip"))
    mac_c = Counter(a.get("mac") for a in addresses if a.get("mac"))
    dup_ips = [{"value": k, "count": v} for k, v in ip_c.items() if v > 1]
    dup_macs = [{"value": k, "count": v} for k, v in mac_c.items() if v > 1]
    missing_mac = sum(1 for a in addresses if not a.get("mac"))
    missing_person = sum(1 for a in addresses if not a.get("person"))
    invalid_ip = sum(1 for a in addresses if a.get("ip") and not is_valid_ip(a["ip"]))
    return {
        "total": len(addresses),
        "dup_ip": dup_ips,
        "dup_mac": dup_macs,
        "missing_mac": missing_mac,
        "missing_person": missing_person,
        "invalid_ip": invalid_ip,
        "score": max(0, 100 - len(dup_ips) * 5 - len(dup_macs) * 5 - missing_mac - missing_person - invalid_ip * 2),
    }
