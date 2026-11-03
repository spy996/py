# -*- coding: utf-8 -*-
"""AI 外发合规网关（策略 B：L1/L2 自动；L3 脱敏+确认；L4 禁止）。

所有 DeepSeek 调用都应经过 post_deepseek()，禁止业务代码直接 requests.post。
- 敏感分级：文件名/路径 + 正文关键词 + 正则（IP/MAC/电话/身份证/邮箱/备案号）
- 脱敏：L2/L3 外发前替换 IP/MAC/电话/邮箱/身份证/银行卡/备案号
- 审计：只记元数据（模块、密级、字符数、动作），不记 prompt/回答正文
"""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlparse

import requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL
from database import SessionLocal
from models import AIAudit, SystemConfig

DEFAULTS = {
    "ai_enabled": "1",
    "ai_mode": "balanced",      # balanced | strict
    "ai_l3_auto": "1",          # L3 自动任务是否允许脱敏后外发
    "ai_redact": "1",
    "ai_max_chars": "50000",
}

L4_NAME = [
    "等保", "等级保护", "测评报告", "测评结论", "拓扑", "接入点", "漏洞", "渗透测试",
    "安全防护方案", "纵向加密", "横向隔离", "堡垒机", "电力监控系统", "生产控制",
]
L4_PATH = [
    "net/reports", "net/topology", "等保测评", "网信设备台账", "流域电站ip", "ip与mac",
]
L4_TEXT = [
    "测评结论", "安全防护方案", "纵向加密", "横向隔离", "堡垒机", "漏洞扫描",
    "渗透测试", "生产控制大区", "安全接入区", "网络拓扑",
]
L3_NAME = [
    "合同", "协议", "发票", "结算", "暂估", "财务", "工资", "人员", "名单", "专利",
    "论文", "软著", "未公开", "内部", "机密", "秘密", "ip", "mac", "地址统计",
    "设备台账", "网信设备",
]
L3_TEXT = [
    "价税合计", "开户行", "银行账号", "身份证号", "手机号码", "合同金额", "合同编号",
    "甲方：", "乙方：", "著作权人", "专利号", "申请号", "设备名称", "所属系统", "使用地点",
]

IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
MAC_RE = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
FILING_RE = re.compile(r"(?<!\d)\d{8,}-\d{4,}(?!\d)")


class AIBlocked(RuntimeError):
    """AI 外发被合规策略拦截。"""


def _as_bool(v, default=True) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _as_int(v, default=50000) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def get_policy() -> dict:
    db = SessionLocal()
    try:
        rows = {r.key: r.value for r in db.query(SystemConfig).filter(SystemConfig.key.like("ai_%")).all()}
    except Exception:
        rows = {}
    finally:
        db.close()
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in rows.items() if v is not None})
    return {
        "enabled": _as_bool(merged.get("ai_enabled"), True),
        "mode": merged.get("ai_mode") or "balanced",
        "l3_auto": _as_bool(merged.get("ai_l3_auto"), True),
        "redact": _as_bool(merged.get("ai_redact"), True),
        "max_chars": _as_int(merged.get("ai_max_chars"), 50000),
    }


def classify_sensitivity(text: str = "", filename: str = "", file_path: str = ""):
    """返回 (level, reasons)。L4 禁止；L3 脱敏+确认；L2 自动（脱敏）；L1 公开。"""
    t = text or ""
    name = f"{filename or ''} {file_path or ''}".lower()
    reasons = []
    if any(k in name for k in L4_NAME) or any(k in name for k in L4_PATH):
        reasons.append("文件名/路径命中 L4 关键词")
        return "L4", reasons
    if any(k in t for k in L4_TEXT):
        reasons.append("正文命中 L4 关键词")
        return "L4", reasons
    if ("等保" in t or "等级保护" in t) and ("测评" in t or "定级" in t):
        reasons.append("正文为等保测评/定级内容")
        return "L4", reasons
    if "拓扑" in t and ("网络" in t or "系统" in t):
        reasons.append("正文为网络拓扑内容")
        return "L4", reasons
    ip_n = len(IP_RE.findall(t))
    mac_n = len(MAC_RE.findall(t))
    if ip_n >= 8 or mac_n >= 4:
        reasons.append(f"正文含大量网络标识（IP {ip_n} / MAC {mac_n}）")
        return "L4", reasons
    if any(k in name for k in L3_NAME):
        reasons.append("文件名命中 L3 关键词")
        return "L3", reasons
    if any(k in t for k in L3_TEXT):
        reasons.append("正文命中 L3 关键词")
        return "L3", reasons
    if "甲方" in t and "乙方" in t and ("金额" in t or "元" in t):
        reasons.append("正文含合同要素")
        return "L3", reasons
    if ip_n or mac_n or PHONE_RE.search(t) or EMAIL_RE.search(t) or ID_RE.search(t):
        reasons.append("正文含 IP/MAC/联系方式等标识")
        return "L3", reasons
    if "公开" in name or "公开" in t[:2000]:
        return "L1", ["公开信息"]
    return "L2", ["内部一般信息"]


def redact(text: str):
    """脱敏 IP/MAC/电话/邮箱/身份证/银行卡/备案号，返回 (text, 命中数)。"""
    if not text:
        return text, 0
    n = 0
    out = text

    def _apply(s, pattern, label):
        nonlocal n

        def repl(m):
            nonlocal n
            n += 1
            return f"[{label}]"
        return pattern.sub(repl, s)

    for pattern, label in ((MAC_RE, "MAC"), (IP_RE, "IP"), (PHONE_RE, "PHONE"),
                           (EMAIL_RE, "EMAIL"), (ID_RE, "ID"), (BANK_RE, "BANK"),
                           (FILING_RE, "FILING")):
        out = _apply(out, pattern, label)
    return out, n


def _audit(module, level, chars, action, reason, ref_type="", ref_id=None,
           model="", request_id="", username="system"):
    try:
        db = SessionLocal()
        try:
            db.add(AIAudit(username=username, module=module, ref_type=ref_type or None,
                           ref_id=ref_id, sensitivity=level, chars=chars, action=action,
                           reason=(reason or "")[:200],
                           endpoint=(urlparse(DEEPSEEK_API_URL).hostname or "")[:100],
                           model=model or "", request_id=request_id))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def _block(module, level, chars, reason, ref_type="", ref_id=None, model=""):
    _audit(module, level, chars, "blocked", reason, ref_type, ref_id, model, str(uuid.uuid4())[:12])
    raise AIBlocked(reason)


def post_deepseek(payload: dict, *, module: str, scan_text: str = None,
                  filename: str = "", file_path: str = "", ref_type: str = "",
                  ref_id: int = None, confirm_l3: bool = False,
                  timeout: int = 120, username: str = "system"):
    """合规网关：检查 → 分级 → 脱敏/拦截 → 发送 → 审计。返回 requests.Response。"""
    p = get_policy()
    msgs = payload.get("messages") or []
    user_idx = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    user_texts = [msgs[i].get("content") or "" for i in user_idx]
    combined = "\n".join(user_texts)
    scan = scan_text if scan_text is not None else combined[-12000:]
    level, reasons = classify_sensitivity(scan, filename, file_path)
    chars = len(combined)
    rid = uuid.uuid4().hex[:12]

    if not p["enabled"]:
        _block(module, level, chars, "AI 外发已关闭（ai_enabled=0）", ref_type, ref_id, payload.get("model", ""))
    if level == "L4":
        _block(module, level, chars,
               "检测到 L4 敏感内容（等保/拓扑/漏洞/生产控制等），按策略禁止外发。",
               ref_type, ref_id, payload.get("model", ""))
    if level == "L3":
        # 只有「ask」逐次确认；后台任务由 ai_l3_auto 控制
        need_confirm = (module == "ask") or (p["mode"] == "strict") or (not p["l3_auto"])
        if need_confirm and not confirm_l3:
            _block(module, level, chars,
                   "检测到 L3 敏感内容，需确认后脱敏外发；请在页面勾选“允许脱敏后使用 AI”。",
                   ref_type, ref_id, payload.get("model", ""))

    redacted_n = 0
    if p["redact"] and level in ("L2", "L3"):
        new_texts, total = [], 0
        for t in user_texts:
            nt, c = redact(t)
            new_texts.append(nt)
            total += c
        if total:
            redacted_n = total
            for i, nt in zip(user_idx, new_texts):
                msgs[i]["content"] = nt
            payload["messages"] = msgs
            combined = "\n".join(new_texts)

    # 字符上限：截断最长的 user 内容
    maxc = p["max_chars"]
    if maxc > 0 and len(combined) > maxc:
        over = len(combined) - maxc
        longest = max(range(len(user_idx)), key=lambda k: len(msgs[user_idx[k]].get("content") or ""))
        idx = user_idx[longest]
        cur = msgs[idx].get("content") or ""
        keep = max(0, len(cur) - over)
        msgs[idx]["content"] = cur[:keep] + "\n…（已按合规策略截断）"
        payload["messages"] = msgs

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=timeout)
    action = "redacted" if redacted_n else "sent"
    reason = f"{level}；{'；'.join(reasons)}；脱敏 {redacted_n} 处" if redacted_n else f"{level}；{'；'.join(reasons)}"
    _audit(module, level, len(combined), action, reason, ref_type, ref_id,
           payload.get("model", ""), rid, username)
    return resp
