# -*- coding: utf-8 -*-
"""网络通讯台账 · Streamlit 页面。

独立模块，避免 app.py 继续膨胀；通过 render_net_page() 挂到主导航。
"""
from __future__ import annotations

import os
from datetime import date

from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

_s = requests.Session()
_s.trust_env = False


# ---------------------------------------------------------------- API 客户端
def _headers():
    tok = st.session_state.get("token", "")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _req(method: str, path: str, **kw):
    kw.setdefault("timeout", 180)
    try:
        return _s.request(method, f"{API_BASE}{path}", headers=_headers(), **kw)
    except Exception as e:
        st.error(f"无法连接后端：{e}")
        return None


def _json(r, default=None):
    if r is None:
        return default
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return default
    st.error(f"请求失败（{r.status_code}）：{r.text[:300]}")
    return default


def _get(path, **params):
    return _json(_req("GET", path, params=params), default=[])


def _post(path, **kw):
    return _json(_req("POST", path, **kw))


def _put(path, **kw):
    return _json(_req("PUT", path, **kw))


def _delete(path):
    return _json(_req("DELETE", path))


def _tok():
    return st.session_state.get("token", "")


def _opts(items, label_key="name", id_key="id", empty=None):
    out = {}
    if empty:
        out[empty] = None
    for x in items:
        out[f"{x.get(label_key, '')}（#{x.get(id_key)}）"] = x.get(id_key)
    return out


# ---------------------------------------------------------------- 总览
def _tab_overview(sites, systems):
    with st.container(border=True):
        st.markdown("**🧠 本地智能问答（不联网、不调用 AI）**")
        c1, c2 = st.columns([5, 1])
        nq = c1.text_input("问：设备数量、IP 冲突、等保符合率、整改逾期、拓扑图、站点清单……",
                           key="net_local_q", label_visibility="collapsed")
        if c2.button("本地查询", key="net_local_btn") and nq.strip():
            res = _post("/net/ask", json={"question": nq})
            if res:
                st.session_state["net_local_answer"] = res
        res = st.session_state.get("net_local_answer")
        if res:
            st.markdown(res.get("answer", ""))
            if res.get("sources"):
                st.caption("来源：" + "；".join(res["sources"][:5]))
    st.divider()

    ov = _get("/net/overview") or {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("站点", ov.get("sites", 0))
    c2.metric("定级系统", ov.get("systems", 0))
    c3.metric("设备", ov.get("devices", 0))
    c4.metric("IP/MAC", ov.get("addresses", 0))
    c5.metric("等保资料", ov.get("reports", 0))
    c6.metric("待整改", ov.get("findings_open", 0))

    q = ov.get("address_quality") or {}
    st.markdown("#### 📋 数据质量")
    qc = st.columns(5)
    qc[0].metric("地址完整度", f"{q.get('score', 0)}%")
    qc[1].metric("重复 IP", len(q.get("dup_ip") or []))
    qc[2].metric("重复 MAC", len(q.get("dup_mac") or []))
    qc[3].metric("缺 MAC", q.get("missing_mac", 0))
    qc[4].metric("缺使用人", q.get("missing_person", 0))
    if q.get("dup_ip") or q.get("dup_mac"):
        with st.expander("查看冲突明细", expanded=False):
            if q.get("dup_ip"):
                st.write("重复 IP：" + "、".join(f"{x['value']}（{x['count']}）" for x in q["dup_ip"]))
            if q.get("dup_mac"):
                st.write("重复 MAC：" + "、".join(f"{x['value']}（{x['count']}）" for x in q["dup_mac"]))

    ev = ov.get("eval_2026") or {}
    st.markdown("#### 🛡️ 2026 年测评结果")
    e1, e2, e3 = st.columns(3)
    e1.metric("已录入系统", ev.get("count", 0))
    e2.metric("平均符合率", f"{ev.get('avg_pass_rate') or 0}%")
    e3.metric("整改问题", ev.get("problem_total", 0))
    low = ev.get("low") or []
    if low:
        st.caption("符合率最低：" + "、".join(f"{x['system_name']} {x['pass_rate']}%" for x in low))

    left, right = st.columns(2)
    with left:
        st.markdown("#### 🗓️ 测评到期（180 天内）")
        ups = ov.get("upcoming") or []
        if not ups:
            st.caption("暂无（先在「站点与系统」填写下次测评日）")
        for u in ups:
            color = "🔴" if u["days"] < 0 else ("🟠" if u["days"] <= 30 else "🟡")
            st.write(f"{color} {u['system_name']}（{u['level']}）：{u['next_eval_date']}，{u['days']} 天")
    with right:
        st.markdown("#### 📦 设备分布")
        cats = ov.get("device_by_category") or []
        if cats:
            st.bar_chart(pd.DataFrame(cats).set_index("category"))
        else:
            st.caption("暂无设备数据")

    st.markdown("#### 🕘 最近变更")
    ch = ov.get("recent_changes") or []
    if ch:
        st.dataframe(pd.DataFrame([{
            "时间": c["ts"], "对象": f"{c['entity_type']}｜{c['label']}", "字段": c["field"],
            "原值": (c["old"] or "")[:40], "新值": (c["new"] or "")[:40], "操作人": c["operator"],
        } for c in ch]), use_container_width=True, hide_index=True)
    else:
        st.caption("暂无变更记录")


# ---------------------------------------------------------------- 站点与系统
def _tab_sites(sites, systems):
    st.markdown("#### 🏢 站点")
    if sites:
        st.dataframe(pd.DataFrame([{
            "编码": s["code"], "站点": s["name"], "公司": s["company"], "类型": s["site_type"],
            "系统数": s["system_count"], "设备数": s["device_count"], "负责人": s["manager"],
        } for s in sites]), use_container_width=True, hide_index=True)
    with st.expander("➕ 新增站点"):
        with st.form("net_site_add"):
            c1, c2, c3 = st.columns(3)
            code = c1.text_input("编码（英文/数字）", placeholder="如 YB")
            name = c2.text_input("站点名称")
            company = c3.selectbox("所属公司", ["木里河公司", "唐央公司", "木里新能", "其他"])
            c4, c5, c6 = st.columns(3)
            stype = c4.selectbox("类型", ["水电站", "光伏电站", "风电场", "开关站", "集控中心", "办公区"])
            manager = c5.text_input("负责人")
            phone = c6.text_input("联系电话")
            ok = st.form_submit_button("保存站点", type="primary")
        if ok:
            r = _post("/net/sites", json={"code": code, "name": name, "company": company,
                                          "site_type": stype, "manager": manager, "phone": phone})
            if r:
                st.success("已新增")
                st.rerun()

    st.divider()
    st.markdown("#### 🛡️ 定级系统")
    rows = [{
        "ID": s["id"], "系统名称": s["name"], "站点": s["site_name"], "等级": s["level"],
        "S/A": f"{s['level_s']}/{s['level_a']}", "备案号": s["filing_no"],
        "设备": s.get("device_count", 0), "资料": s.get("report_count", 0),
        "下次测评": s["next_eval_date"], "责任人": s["manager"],
    } for s in systems]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if systems:
        sel = st.selectbox("编辑系统", _opts(systems).keys(), key="net_sys_edit_sel")
        s = next(x for x in systems if x["id"] == _opts(systems)[sel])
        with st.form(f"net_sys_edit_{s['id']}"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("系统名称", value=s["name"])
            level = c2.selectbox("等级", ["二级", "三级"], index=1 if s["level"] == "三级" else 0)
            manager = c3.text_input("责任人", value=s["manager"])
            c4, c5, c6 = st.columns(3)
            filing = c4.text_input("备案号", value=s["filing_no"])
            next_eval = c5.text_input("下次测评日", value=s["next_eval_date"], placeholder="YYYY-MM-DD")
            zone = c6.text_input("部署大区", value=s["deploy_zone"])
            ok = st.form_submit_button("保存修改")
        if ok:
            ls, la = ("S3", "A3") if level == "三级" else ("S2", "A2")
            r = _put(f"/net/systems/{s['id']}", json={"name": name, "level": level, "level_s": ls, "level_a": la,
                                                      "manager": manager, "filing_no": filing,
                                                      "next_eval_date": next_eval, "deploy_zone": zone})
            if r:
                st.success("已保存")
                st.rerun()

    with st.expander("➕ 新增定级系统"):
        with st.form("net_sys_add"):
            site_sel = st.selectbox("所属站点", _opts(sites).keys())
            name = st.text_input("系统名称")
            c1, c2, c3 = st.columns(3)
            level = c1.selectbox("等级", ["二级", "三级"])
            filing = c2.text_input("备案号")
            manager = c3.text_input("责任人")
            ok = st.form_submit_button("保存系统", type="primary")
        if ok and name:
            ls, la = ("S3", "A3") if level == "三级" else ("S2", "A2")
            r = _post("/net/systems", json={"site_id": _opts(sites)[site_sel], "name": name,
                                            "level": level, "level_s": ls, "level_a": la,
                                            "filing_no": filing, "manager": manager})
            if r:
                st.success("已新增")
                st.rerun()


# ---------------------------------------------------------------- 设备
def _tab_devices(sites, systems):
    st.markdown("#### 🖥️ 设备台账")
    site_map = _opts(sites, empty="（全部站点）")
    c1, c2, c3, c4, c5 = st.columns([2, 2, 1.5, 1.2, 2])
    site_lbl = c1.selectbox("站点", site_map.keys(), key="net_dev_site")
    site_id = site_map[site_lbl]
    sys_opts = {"（全部系统）": None}
    sys_opts.update({s["name"]: s["id"] for s in systems if (not site_id or s["site_id"] == site_id)})
    sys_lbl = c2.selectbox("系统", sys_opts.keys(), key="net_dev_sys")
    cat = c3.selectbox("类别", ["（全部）", "网络设备", "安全设备", "服务器存储", "终端设备", "其他设备"])
    status = c4.selectbox("状态", ["（全部）", "在用", "备用", "维修", "报废"])
    q = c5.text_input("关键词（名称/型号/IP/MAC）", key="net_dev_q")

    params = {"site_id": site_id, "system_id": sys_opts[sys_lbl], "limit": 2000}
    if cat != "（全部）":
        params["category"] = cat
    if status != "（全部）":
        params["status"] = status
    if q.strip():
        params["q"] = q.strip()
    data = _get("/net/devices", **params) or {}
    items = data.get("items") or []
    st.caption(f"共 {data.get('total', 0)} 台")
    if items:
        df = pd.DataFrame([{
            "ID": d["id"], "站点": d["site_name"], "系统": d["system_name"], "类别": d["category"],
            "设备名称": d["name"], "品牌型号": d["brand_model"], "区域": d["zone"],
            "IP": d["mgmt_ip"], "状态": d["status"], "数量": d["quantity"],
        } for d in items])
        evt = st.dataframe(df, use_container_width=True, hide_index=True,
                           on_select="rerun", selection_mode="single-row", key="net_dev_table")
        row_idx = evt.selection.rows[0] if evt.selection and evt.selection.rows else None
        if row_idx is not None and row_idx < len(items):
            _device_detail(items[row_idx], sites, systems)
    else:
        st.info("暂无设备，请在「导入导出」页导入调研表，或在下方新增。")

    if items:
        with st.expander("🔧 批量修改选中设备"):
            labels = {f"#{d['id']}｜{d['site_name']}｜{d['name']}": d["id"] for d in items}
            sel = st.multiselect("选择设备（可多选）", list(labels.keys()), key="net_dev_batch_sel")
            c1, c2, c3 = st.columns(3)
            field = c1.selectbox("修改字段", ["定级系统", "网络区域", "重要性", "状态", "责任人", "物理环境"],
                                 key="net_dev_batch_field")
            value = ""
            field_key = ""
            if field == "定级系统":
                v = c2.selectbox("新值", _opts(systems).keys(), key="net_dev_batch_sys")
                value, field_key = _opts(systems)[v], "system_id"
            elif field == "重要性":
                value, field_key = c2.selectbox("新值", ["关键", "重要", "一般"], key="net_dev_batch_imp"), "importance"
            elif field == "状态":
                value, field_key = c2.selectbox("新值", ["在用", "备用", "维修", "报废"], key="net_dev_batch_status"), "status"
            elif field == "网络区域":
                value, field_key = c2.selectbox(
                    "新值", ["安全区Ⅰ", "安全区Ⅱ", "安全区Ⅲ", "安全区Ⅰ/Ⅱ", "生产控制大区", "管理信息大区"],
                    key="net_dev_batch_zone"), "zone"
            else:
                value = c2.text_input("新值", key="net_dev_batch_text")
                field_key = "owner" if field == "责任人" else "room"
            if c3.button("应用批量修改", key="net_dev_batch_btn"):
                if not sel:
                    st.warning("请先选择设备")
                else:
                    res = _post("/net/devices/batch-update", json={
                        "ids": [labels[x] for x in sel], "field": field_key, "value": value})
                    if res:
                        st.success(f"已修改 {res.get('updated', 0)} 台")
                        st.rerun()

    with st.expander("➕ 新增设备"):
        with st.form("net_dev_add"):
            c1, c2, c3 = st.columns(3)
            s_lbl = c1.selectbox("站点", _opts(sites).keys(), key="net_dev_add_site")
            s_id = _opts(sites)[s_lbl]
            sy_opts = {"（不关联系统）": None}
            sy_opts.update({s["name"]: s["id"] for s in systems if s["site_id"] == s_id})
            sy_lbl = c2.selectbox("定级系统", sy_opts.keys(), key="net_dev_add_sys")
            cat2 = c3.selectbox("类别", ["网络设备", "安全设备", "服务器存储", "终端设备", "其他设备"])
            c4, c5, c6 = st.columns(3)
            name = c4.text_input("设备名称")
            brand = c5.text_input("品牌及型号")
            zone = c6.selectbox("所属网络区域", ["安全区Ⅰ", "安全区Ⅱ", "安全区Ⅲ", "安全区Ⅰ/Ⅱ", "生产控制大区", "管理信息大区"])
            c7, c8, c9 = st.columns(3)
            ip = c7.text_input("IP 地址")
            mac = c8.text_input("MAC")
            owner = c9.text_input("责任人")
            ok = st.form_submit_button("保存设备", type="primary")
        if ok and name:
            r = _post("/net/devices", json={"site_id": s_id, "system_id": sy_opts[sy_lbl],
                                            "category": cat2, "name": name, "brand_model": brand,
                                            "zone": zone, "mgmt_ip": ip, "mac": mac, "owner": owner})
            if r:
                st.success("已新增")
                st.rerun()


def _device_detail(d, sites, systems):
    with st.container(border=True):
        st.markdown(f"### {d['name']}  `#{d['id']}`")
        c1, c2, c3 = st.columns(3)
        c1.write(f"**站点**：{d['site_name']}")
        c1.write(f"**系统**：{d['system_name'] or '—'}")
        c1.write(f"**类别**：{d['category']}")
        c2.write(f"**品牌型号**：{d['brand_model'] or '—'}")
        c2.write(f"**区域**：{d['zone'] or '—'}")
        c2.write(f"**物理环境**：{d['room'] or '—'}")
        c3.write(f"**IP**：{d['mgmt_ip'] or '—'}")
        c3.write(f"**MAC**：{d['mac'] or '—'}")
        c3.write(f"**状态**：{d['status']}｜重要性 {d['importance']}")
        with st.form(f"net_dev_edit_{d['id']}"):
            c1, c2, c3 = st.columns(3)
            zone = c1.text_input("区域", value=d["zone"])
            room = c2.text_input("物理环境", value=d["room"])
            ip = c3.text_input("IP", value=d["mgmt_ip"])
            c4, c5, c6 = st.columns(3)
            mac = c4.text_input("MAC", value=d["mac"])
            owner = c5.text_input("责任人", value=d["owner"])
            status = c6.selectbox("状态", ["在用", "备用", "维修", "报废"],
                                  index=["在用", "备用", "维修", "报废"].index(d["status"]) if d["status"] in ("在用", "备用", "维修", "报废") else 0)
            remark = st.text_area("备注", value=d["remark"], height=70)
            ok = st.form_submit_button("保存修改")
        if ok:
            r = _put(f"/net/devices/{d['id']}", json={"zone": zone, "room": room, "mgmt_ip": ip,
                                                      "mac": mac, "owner": owner, "status": status,
                                                      "remark": remark})
            if r:
                st.success("已保存")
                st.rerun()


# ---------------------------------------------------------------- IP/MAC
def _tab_addresses(sites):
    st.markdown("#### 🌐 IP / MAC 台账")
    c1, c2, c3, c4 = st.columns([2, 2, 1.4, 2])
    site_map = _opts(sites, empty="（全部站点）")
    site_lbl = c1.selectbox("站点", site_map.keys(), key="net_addr_site")
    dept = c2.text_input("部门（精确）", key="net_addr_dept")
    status = c3.selectbox("状态", ["（全部）", "在用", "停用", "未接网线", "待核实"])
    q = c4.text_input("关键词（IP/MAC/姓名/地点）", key="net_addr_q")
    conflict_only = st.checkbox("只看冲突（重复 IP / MAC）", key="net_addr_conflict")

    params = {"site_id": site_map[site_lbl], "limit": 3000, "conflict_only": conflict_only}
    if dept.strip():
        params["department"] = dept.strip()
    if status != "（全部）":
        params["status"] = status
    if q.strip():
        params["q"] = q.strip()
    data = _get("/net/addresses", **params) or {}
    items = data.get("items") or []
    conf = data.get("conflicts") or {}
    st.caption(f"共 {data.get('total', 0)} 条｜重复 IP {len(conf.get('dup_ip') or {})} 组｜重复 MAC {len(conf.get('dup_mac') or {})} 组")
    if items:
        df = pd.DataFrame([{
            "ID": a["id"], "IP": a["ip"], "MAC": a["mac"], "部门": a["department"], "姓名": a["person"],
            "类型": a["device_type"], "地点": a["location"], "状态": a["status"],
            "网鉴": a["wangjian"], "备注": a["remark"],
        } for a in items])
        evt = st.dataframe(df, use_container_width=True, hide_index=True,
                           on_select="rerun", selection_mode="single-row", key="net_addr_table")
        row_idx = evt.selection.rows[0] if evt.selection and evt.selection.rows else None
        if row_idx is not None and row_idx < len(items):
            a = items[row_idx]
            with st.form(f"net_addr_edit_{a['id']}"):
                c1, c2, c3 = st.columns(3)
                ip = c1.text_input("IP", value=a["ip"])
                mac = c2.text_input("MAC", value=a["mac"])
                person = c3.text_input("使用人", value=a["person"])
                c4, c5, c6 = st.columns(3)
                dpt = c4.text_input("部门", value=a["department"])
                loc = c5.text_input("使用地点", value=a["location"])
                stt = c6.selectbox("状态", ["在用", "停用", "未接网线", "待核实"],
                                   index=["在用", "停用", "未接网线", "待核实"].index(a["status"]) if a["status"] in ("在用", "停用", "未接网线", "待核实") else 0)
                remark = st.text_area("备注", value=a["remark"], height=60)
                ok = st.form_submit_button("保存修改")
            if ok:
                r = _put(f"/net/addresses/{a['id']}", json={"ip": ip, "mac": mac, "person": person,
                                                            "department": dpt, "location": loc,
                                                            "status": stt, "remark": remark})
                if r:
                    st.success("已保存")
                    st.rerun()

    with st.expander("➕ 新增地址"):
        with st.form("net_addr_add"):
            c1, c2, c3 = st.columns(3)
            site_lbl2 = c1.selectbox("站点", _opts(sites).keys())
            ip = c2.text_input("IP")
            mac = c3.text_input("MAC")
            c4, c5, c6 = st.columns(3)
            dpt = c4.text_input("部门")
            person = c5.text_input("使用人")
            loc = c6.text_input("使用地点")
            ok = st.form_submit_button("保存地址", type="primary")
        if ok and (ip or mac):
            r = _post("/net/addresses", json={"site_id": _opts(sites)[site_lbl2], "ip": ip, "mac": mac,
                                              "department": dpt, "person": person, "location": loc})
            if r:
                st.success("已新增")
                st.rerun()

    if st.button("🧹 一键规范化 MAC / 去空格", key="net_addr_norm"):
        r = _post("/net/addresses/normalize")
        if r:
            st.success(f"已修复 {r.get('fixed', 0)} 条")
            st.rerun()


# ---------------------------------------------------------------- 数据质量
def _tab_quality(sites, systems):
    q = _get("/net/quality") or {}
    a = q.get("address") or {}
    d = q.get("device") or {}
    st.markdown("#### 🧹 数据质量闭环")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("地址完整度", f"{a.get('score', 0)}%")
    c2.metric("重复 IP", len(a.get("dup_ip") or []))
    c3.metric("重复 MAC", len(a.get("dup_mac") or []))
    c4.metric("缺 MAC", a.get("missing_mac", 0))
    c5.metric("缺部门/地点", (a.get("missing_department", 0) or 0) + (a.get("missing_location", 0) or 0))
    c6.metric("设备待完善", (d.get("missing_system", 0) or 0) + (d.get("missing_zone", 0) or 0)
              + (d.get("missing_room", 0) or 0))

    st.divider()
    st.markdown("#### 🏷️ 部门 / 地点归一")
    kind_label = st.radio("归一类型", ["部门", "地点"], horizontal=True, key="net_q_kind")
    kind = "department" if kind_label == "部门" else "location"
    dicts = _get("/net/dicts", kind=kind) or []
    sug = _get("/net/dicts/suggest", kind=kind) or {}
    items = sug.get("items") or []
    unmatched = [x for x in items if not x.get("matched")]
    left, right = st.columns([1.15, 1])

    with left:
        st.markdown(f"**归一字典（{len(dicts)} 项）**")
        if dicts:
            st.dataframe(pd.DataFrame([{"名称": x["name"], "别名": x["alias"]} for x in dicts]),
                         use_container_width=True, hide_index=True)
        with st.expander("➕ 新增归一规则"):
            with st.form(f"net_q_add_{kind}"):
                raw_opts = [x["value"] for x in unmatched] or ["（无待归一值）"]
                raw = st.selectbox("原始值", raw_opts, key=f"net_q_raw_{kind}")
                canonical = st.text_input("归一为", value="" if raw == "（无待归一值）" else raw,
                                          key=f"net_q_canonical_{kind}")
                extra = st.text_input("其他别名（逗号分隔，可空）", key=f"net_q_extra_{kind}")
                ok = st.form_submit_button("保存规则")
            if ok and canonical.strip():
                aliases = [raw] + [x.strip() for x in extra.split(",") if x.strip()]
                res = _post("/net/dicts", json={"kind": kind, "name": canonical.strip(),
                                                "alias": ",".join([x for x in aliases if x and x != "（无待归一值）"])})
                if res:
                    st.success("已保存")
                    st.rerun()
        if dicts:
            del_sel = st.selectbox("删除规则", [f"{x['name']}｜{x['alias']}" for x in dicts],
                                   key=f"net_q_del_{kind}")
            if st.button("删除该规则", key=f"net_q_delbtn_{kind}"):
                target = next(x for x in dicts if del_sel.startswith(x["name"] + "｜"))
                if _delete(f"/net/dicts/{target['id']}"):
                    st.success("已删除")
                    st.rerun()

    with right:
        st.markdown(f"**待归一（{len(unmatched)} 种 / {sum(x['count'] for x in unmatched)} 条）**")
        if unmatched:
            st.dataframe(pd.DataFrame([{"原始值": x["value"], "条数": x["count"]} for x in unmatched[:40]]),
                         use_container_width=True, hide_index=True)
        else:
            st.success("当前类型已全部归一 ✅")
        if st.button(f"✅ 按字典归一（{kind_label}）", type="primary", key=f"net_q_apply_{kind}"):
            res = _post("/net/addresses/apply-dict", json={"kind": kind})
            if res:
                st.success(f"已归一 {res.get('changed', 0)} 条")
                st.rerun()

    st.divider()
    st.markdown("#### 🔎 问题清单")
    t1, t2, t3, t4 = st.tabs(["重复 IP", "重复 MAC", "缺字段地址", "设备待完善"])
    with t1:
        if a.get("dup_ip"):
            st.dataframe(pd.DataFrame([{"IP": x["value"], "条数": x["count"]} for x in a["dup_ip"]]),
                         use_container_width=True, hide_index=True)
        else:
            st.success("无重复 IP")
    with t2:
        if a.get("dup_mac"):
            st.dataframe(pd.DataFrame([{"MAC": x["value"], "条数": x["count"]} for x in a["dup_mac"]]),
                         use_container_width=True, hide_index=True)
        else:
            st.success("无重复 MAC")
    with t3:
        addr_data = _get("/net/addresses", limit=3000) or {}
        addr_items = addr_data.get("items") or []
        miss = [x for x in addr_items if not x["mac"] or not x["person"] or not x["department"] or not x["location"]]
        if miss:
            st.caption(f"共 {len(miss)} 条存在缺失字段（显示前 100 条）")
            st.dataframe(pd.DataFrame([{
                "ID": x["id"], "IP": x["ip"], "MAC": x["mac"], "部门": x["department"],
                "姓名": x["person"], "地点": x["location"], "状态": x["status"],
            } for x in miss[:100]]), use_container_width=True, hide_index=True)
        else:
            st.success("地址字段完整 ✅")
    with t4:
        dev_data = _get("/net/devices", limit=2000) or {}
        dev_items = dev_data.get("items") or []
        dev_miss = [x for x in dev_items if not x["system_id"] or not x["zone"] or not x["room"]]
        if dev_miss:
            st.caption(f"共 {len(dev_miss)} 台设备存在缺失字段（显示前 100 台）")
            st.dataframe(pd.DataFrame([{
                "ID": x["id"], "站点": x["site_name"], "设备": x["name"], "系统": x["system_name"],
                "区域": x["zone"], "责任人": x["owner"], "物理环境": x["room"],
            } for x in dev_miss[:100]]), use_container_width=True, hide_index=True)
        else:
            st.success("设备关键字段完整 ✅")

    st.divider()
    st.markdown("#### ⚡ 一键修复")
    f1, f2 = st.columns(2)
    if f1.button("🧹 MAC 规范化 / 去空格", key="net_q_fix_mac"):
        res = _post("/net/addresses/normalize")
        if res:
            st.success(f"已修复 {res.get('fixed', 0)} 条")
            st.rerun()
    if f2.button("🧹 部门 + 地点全部归一", key="net_q_fix_all"):
        res = _post("/net/addresses/apply-dict", json={"kind": "all"})
        if res:
            st.success(f"已归一 {res.get('changed', 0)} 条")
            st.rerun()


# ---------------------------------------------------------------- 拓扑图
def _tab_topologies(sites, systems):
    st.markdown("#### 🗺️ 拓扑图")
    sys_map = {"（全部系统）": None}
    sys_map.update({s["name"]: s["id"] for s in systems})
    sel = st.selectbox("系统筛选", sys_map.keys(), key="net_topo_sys")
    items = _get("/net/topologies", system_id=sys_map[sel]) or []
    if not items:
        st.info("暂无拓扑图，请在下方上传 JPG/PNG。")
    token = _tok()
    for t in items:
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            with c1:
                st.image(f"{API_BASE}/net/topologies/{t['id']}/image?token={token}", use_container_width=True)
            with c2:
                st.markdown(f"**{t['name']}**")
                st.write(f"系统：{t['system_name'] or '—'}")
                st.write(f"类型：{t['kind']}｜版本：{t['version'] or '—'}")
                st.write(f"生效日期：{t['effective_date'] or '—'}")
                if t["note"]:
                    st.caption(t["note"])
                st.link_button("⬇️ 下载原图", f"{API_BASE}/net/topologies/{t['id']}/download?token={token}")
                if t["has_source"]:
                    st.link_button("⬇️ 下载源文件", f"{API_BASE}/net/topologies/{t['id']}/download?source=1&token={token}")

    with st.expander("⬆️ 上传拓扑图"):
        with st.form("net_topo_upload"):
            c1, c2 = st.columns(2)
            img = c1.file_uploader("拓扑图图片（JPG/PNG）", type=["jpg", "jpeg", "png", "bmp"], key="net_topo_img")
            src = c2.file_uploader("源文件（可选，vsd/vsdx/drawio）", type=["vsd", "vsdx", "drawio", "xml"], key="net_topo_src")
            c3, c4, c5 = st.columns(3)
            sys_sel = c3.selectbox("关联系统（可留空自动匹配）", ["（自动匹配）"] + list(_opts(systems).keys()))
            kind = c4.selectbox("类型", ["网络拓扑图", "接入点图", "总体拓扑"])
            version = c5.text_input("版本", value=str(date.today().year))
            c6, c7 = st.columns(2)
            eff = c6.text_input("生效日期", placeholder="YYYY-MM-DD")
            note = c7.text_input("变更说明")
            ok = st.form_submit_button("上传", type="primary")
        if ok and img is not None:
            files = {"image": (img.name, img.getvalue())}
            if src is not None:
                files["source"] = (src.name, src.getvalue())
            data = {"kind": kind, "version": version, "effective_date": eff, "note": note}
            if sys_sel != "（自动匹配）":
                data["system_id"] = _opts(systems)[sys_sel]
            r = _req("POST", "/net/topologies", files=files, data=data)
            res = _json(r)
            if res:
                st.success(f"已上传：{res.get('system_name') or '未匹配系统'}")
                st.rerun()


# ---------------------------------------------------------------- 等保资料
def _tab_reports(sites, systems):
    st.markdown("#### 🛡️ 等保资料")
    year = st.selectbox("年度", [2026, 2025, 2024, 2023], key="net_rep_year")
    summ = _get("/net/reports/summary", year=year) or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("已录入系统", summ.get("count", 0))
    c2.metric("平均符合率", f"{summ.get('avg_pass_rate') or 0}%")
    c3.metric("整改问题", summ.get("problem_total", 0))
    if summ.get("systems"):
        st.dataframe(pd.DataFrame([{
            "系统": x["system_name"], "等级": x["level"], "符合率": x["pass_rate"],
            "问题数": x["problem_count"], "结论": x["conclusion"], "测评结束": x["eval_end"],
            "已确认": "✅" if x["confirmed"] else "⏳",
        } for x in summ["systems"]]), use_container_width=True, hide_index=True)

    reports = _get("/net/reports", year=year) or []
    st.caption(f"本年度资料 {len(reports)} 份")
    if reports:
        st.dataframe(pd.DataFrame([{
            "ID": r["id"], "系统": r["system_name"], "类型": r["doc_type"], "年度": r["report_year"],
            "等级": r["level"], "符合率": r["pass_rate"], "结论": r["conclusion"],
            "文件": r["original_name"], "主/附": r["file_role"], "确认": "✅" if r["confirmed"] else "⏳",
        } for r in reports]), use_container_width=True, hide_index=True)

        # 编辑 + 确认
        sel = st.selectbox("编辑资料", _opts(reports, label_key="original_name").keys(), key="net_rep_sel")
        rid = _opts(reports, label_key="original_name")[sel]
        r = next(x for x in reports if x["id"] == rid)
        with st.form(f"net_rep_edit_{rid}"):
            c1, c2, c3 = st.columns(3)
            dtype = c1.selectbox("资料类型", ["测评报告", "备案证明", "整改报告", "自查报告", "其他"],
                                 index=["测评报告", "备案证明", "整改报告", "自查报告", "其他"].index(r["doc_type"]) if r["doc_type"] in ["测评报告", "备案证明", "整改报告", "自查报告", "其他"] else 4)
            conclusion = c2.selectbox("结论", ["", "符合", "基本符合", "不符合", "良", "中", "差"],
                                      index=0 if not r["conclusion"] else (["", "符合", "基本符合", "不符合", "良", "中", "差"].index(r["conclusion"]) if r["conclusion"] in ["", "符合", "基本符合", "不符合", "良", "中", "差"] else 0))
            pass_rate = c3.number_input("符合率 %", min_value=0.0, max_value=100.0,
                                        value=float(r["pass_rate"] or 0), step=0.01)
            c4, c5, c6 = st.columns(3)
            agency = c4.text_input("测评机构", value=r["agency"])
            eval_end = c5.text_input("测评结束日", value=r["eval_end"])
            next_due = c6.text_input("下次测评日", value=r["next_due"])
            ok = st.form_submit_button("保存修改")
        if ok:
            rr = _put(f"/net/reports/{rid}", json={"doc_type": dtype, "conclusion": conclusion,
                                                   "pass_rate": pass_rate, "agency": agency,
                                                   "eval_end": eval_end, "next_due": next_due})
            if rr:
                st.success("已保存")
                st.rerun()
        if not r["confirmed"] and st.button("✅ 确认该资料（同步系统测评信息）", key=f"net_rep_confirm_{rid}"):
            rr = _put(f"/net/reports/{rid}", json={"confirmed": 1})
            if rr:
                st.success("已确认")
                st.rerun()

    with st.expander("⬆️ 上传等保资料（自动识别系统/类型/年度）", expanded=False):
        ups = st.file_uploader("选择文件（可多选，PDF/Word/图片）", type=["pdf", "docx", "doc", "jpg", "jpeg", "png"],
                               accept_multiple_files=True, key="net_rep_files")
        c1, c2 = st.columns(2)
        sys_sel = c1.selectbox("指定系统（可留空自动匹配）", ["（自动匹配）"] + list(_opts(systems).keys()))
        dtype_sel = c2.selectbox("指定类型（可留空自动判断）", ["（自动判断）", "测评报告", "备案证明", "整改报告", "自查报告", "其他"])
        if st.button("开始上传并识别", type="primary", key="net_rep_upload_btn") and ups:
            files = [("files", (f.name, f.getvalue())) for f in ups]
            data = {}
            if sys_sel != "（自动匹配）":
                data["system_id"] = _opts(systems)[sys_sel]
            if dtype_sel != "（自动判断）":
                data["doc_type"] = dtype_sel
            r = _req("POST", "/net/reports/upload", files=files, data=data)
            res = _json(r)
            if res:
                okn = sum(1 for x in res["results"] if x.get("ok"))
                st.success(f"已上传 {okn}/{len(res['results'])} 份")
                for x in res["results"]:
                    if x.get("ok"):
                        st.write(f"✅ {x['file']} → {x['system_name']}｜{x['doc_type']}｜{x['year']}｜置信度 {x['confidence']}")
                    else:
                        st.warning(f"⚠️ {x['file']}：{x.get('error')}")
                st.rerun()

    st.divider()
    st.markdown("#### ✅ 整改清单")
    rep_sel = _opts([x for x in reports if x["doc_type"] == "测评报告"], label_key="original_name")
    if rep_sel:
        sel2 = st.selectbox("选择测评报告", rep_sel.keys(), key="net_find_rep")
        rid2 = rep_sel[sel2]
        c1, c2 = st.columns(2)
        if c1.button("🔍 解析整改清单（docx）", key="net_find_preview"):
            res = _post(f"/net/reports/{rid2}/findings/preview")
            if res:
                st.session_state["net_find_items"] = res.get("items") or []
                st.session_state["net_find_rid"] = rid2
                st.success(f"解析到 {res.get('count', 0)} 项")
        if st.session_state.get("net_find_rid") == rid2 and st.session_state.get("net_find_items"):
            items = st.session_state["net_find_items"]
            st.dataframe(pd.DataFrame([{
                "编号": x.get("problem_no"), "安全类": x.get("security_class"), "问题": x.get("description")[:60],
                "风险": x.get("risk_level"), "前次": x.get("prev_status"),
            } for x in items]), use_container_width=True, hide_index=True)
            if c2.button("📥 导入为整改任务", type="primary", key="net_find_apply"):
                res = _post(f"/net/reports/{rid2}/findings/apply", json={"items": items, "replace": True})
                if res:
                    st.success(f"已导入 {res.get('inserted', 0)} 项")
                    st.session_state.pop("net_find_items", None)
                    st.rerun()

    fl = _get("/net/findings") or []
    if fl:
        c1, c2 = st.columns(2)
        stt = c1.selectbox("整改状态", ["（全部）", "待整改", "整改中", "已整改", "已验收"], key="net_find_status")
        risk = c2.selectbox("风险等级", ["（全部）", "高", "中", "低"], key="net_find_risk")
        flt = [x for x in fl if (stt == "（全部）" or x["status"] == stt) and (risk == "（全部）" or x["risk_level"] == risk)]
        st.caption(f"共 {len(flt)} 项（全部 {len(fl)} 项）")
        if flt:
            st.dataframe(pd.DataFrame([{
                "ID": x["id"], "系统": x["system_name"], "编号": x["problem_no"], "安全类": x["security_class"],
                "问题": x["description"][:50], "风险": x["risk_level"], "状态": x["status"],
                "责任人": x["owner"], "计划": x["plan_date"],
            } for x in flt]), use_container_width=True, hide_index=True)
            with st.expander("🔧 批量更新整改项"):
                flabels = {f"#{x['id']}｜{x['system_name']}｜问题{x['problem_no']}": x["id"] for x in flt}
                fsel = st.multiselect("选择整改项（可多选）", list(flabels.keys()), key="net_find_batch_sel")
                b1, b2, b3, b4 = st.columns(4)
                bstatus = b1.selectbox("状态", ["", "待整改", "整改中", "已整改", "已验收"], key="net_find_batch_status")
                bowner = b2.text_input("责任人（留空不改）", key="net_find_batch_owner")
                bplan = b3.text_input("计划完成（留空不改）", key="net_find_batch_plan", placeholder="YYYY-MM-DD")
                brisk = b4.selectbox("风险等级", ["", "高", "中", "低"], key="net_find_batch_risk")
                if st.button("应用批量更新", key="net_find_batch_btn"):
                    if not fsel:
                        st.warning("请先选择整改项")
                    else:
                        payload = {"ids": [flabels[x] for x in fsel]}
                        for k, v in (("status", bstatus), ("owner", bowner), ("plan_date", bplan), ("risk_level", brisk)):
                            if v:
                                payload[k] = v
                        res = _post("/net/findings/batch-update", json=payload)
                        if res:
                            st.success(f"已更新 {res.get('updated', 0)} 项")
                            st.rerun()

            sel3 = st.selectbox("更新整改项", [f"#{x['id']}｜{x['system_name']}｜问题{x['problem_no']}" for x in flt],
                                key="net_find_edit_sel")
            f = next(x for x in flt if sel3.startswith(f"#{x['id']}｜"))
            with st.form(f"net_find_edit_{f['id']}"):
                c1, c2, c3 = st.columns(3)
                stt2 = c1.selectbox("状态", ["待整改", "整改中", "已整改", "已验收"],
                                    index=["待整改", "整改中", "已整改", "已验收"].index(f["status"]) if f["status"] in ["待整改", "整改中", "已整改", "已验收"] else 0)
                owner = c2.text_input("责任人", value=f["owner"])
                plan = c3.text_input("计划完成", value=f["plan_date"])
                ok = st.form_submit_button("保存")
            if ok:
                rr = _put(f"/net/findings/{f['id']}", json={"status": stt2, "owner": owner, "plan_date": plan})
                if rr:
                    st.success("已保存")
                    st.rerun()
    st.link_button("⬇️ 导出整改清单", f"{API_BASE}/net/findings/export?token={_tok()}")

    st.divider()
    st.markdown("#### 📊 合规矩阵")
    mat = _get("/net/compliance-matrix", years="2024,2025,2026") or {}
    if mat.get("rows"):
        rows = []
        for x in mat["rows"]:
            row = {"系统": x["system_name"], "等级": x["level"]}
            for y in mat["years"]:
                cell = x["cells"].get(str(y), {})
                row[str(y)] = " ".join(f"{t[:2]}{'✅' if v else '—'}" for t, v in cell.items())
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


    st.divider()
    st.markdown("#### 📄 调研表反向生成")
    st.caption("以公司上一版调研表为模板，刷新「5 类设备表 + 信息系统基本情况」；叙述性章节沿用模板，提交前请人工核对。")
    c1, c2, c3 = st.columns([1.3, 2.2, 1.3])
    scope = c1.radio("范围", ["按公司", "按单个系统"], horizontal=True, key="net_form_scope")
    if scope == "按公司":
        comp = c2.selectbox("公司", ["木里河公司", "唐央公司", "木里新能"], key="net_form_company")
        url = f"{API_BASE}/net/export/investigation?company={quote(comp)}&token={_tok()}"
        caption = f"将生成：{comp} 全部系统的调研表（设备表按台账刷新）"
    else:
        sel = c2.selectbox("系统", _opts(systems).keys(), key="net_form_system")
        sid = _opts(systems)[sel]
        url = f"{API_BASE}/net/export/investigation?system_id={sid}&token={_tok()}"
        caption = "将生成：单系统版本（设备表仅含该系统；公司级章节沿用模板）"
    c3.link_button("⬇️ 生成并下载", url, use_container_width=True)
    st.caption(caption + "｜已刷新：信息系统基本情况、网络设备、安全设备、服务器存储设备、终端设备、其他设备；"
               "未刷新：物理环境、外联线路、网络结构、拓扑图、人员、文档等。")


# ---------------------------------------------------------------- 导入导出
def _tab_io(sites, systems):
    st.markdown("#### 📥 设备导入（调研表）")
    f = st.file_uploader("选择调研表 xlsx", type=["xlsx", "xlsm"], key="net_dev_import_file")
    if st.button("预览导入", key="net_dev_import_preview") and f is not None:
        r = _req("POST", "/net/devices/import/preview", files={"file": (f.name, f.getvalue())})
        res = _json(r)
        if res:
            st.session_state["net_dev_rows"] = res
            st.success(f"共 {res['total']} 条：新增 {res['new']} / 更新 {res['update']} / 跳过 {res['skipped']}")
            if res.get("unknown_systems"):
                st.warning("未匹配系统：" + "、".join(res["unknown_systems"]))
    pv = st.session_state.get("net_dev_rows")
    if pv and pv.get("rows"):
        st.dataframe(pd.DataFrame([{
            "站点": x.get("_site_name"), "系统": x.get("_system_name"), "类别": x.get("category"),
            "设备": x.get("name"), "型号": x.get("brand_model"), "数量": x.get("quantity"),
            "动作": x.get("_action"),
        } for x in pv["rows"]]), use_container_width=True, hide_index=True)
        if st.button("✅ 应用导入", type="primary", key="net_dev_import_apply"):
            res = _post("/net/devices/import/apply", json={"rows": pv["rows"], "file_name": pv.get("file_name")})
            if res:
                st.success(f"已写入：新增 {res['inserted']} / 更新 {res['updated']}")
                st.session_state.pop("net_dev_rows", None)
                st.rerun()

    st.divider()
    st.markdown("#### 📥 IP/MAC 导入")
    f2 = st.file_uploader("选择 IP/MAC 统计表 xlsx", type=["xlsx", "xlsm"], key="net_addr_import_file")
    if st.button("预览导入", key="net_addr_import_preview") and f2 is not None:
        r = _req("POST", "/net/addresses/import/preview", files={"file": (f2.name, f2.getvalue())})
        res = _json(r)
        if res:
            st.session_state["net_addr_rows"] = res
            st.success(f"共 {res['total']} 条：新增 {res['new']} / 更新 {res['update']}")
    pv2 = st.session_state.get("net_addr_rows")
    if pv2 and pv2.get("rows"):
        st.dataframe(pd.DataFrame([{
            "IP": x.get("ip"), "MAC": x.get("mac"), "部门": x.get("department"),
            "姓名": x.get("person"), "地点": x.get("location"), "站点": x.get("_site_name"),
            "动作": x.get("_action"),
        } for x in pv2["rows"]]), use_container_width=True, hide_index=True)
        if st.button("✅ 应用导入", type="primary", key="net_addr_import_apply"):
            res = _post("/net/addresses/import/apply", json={"rows": pv2["rows"], "file_name": pv2.get("file_name")})
            if res:
                st.success(f"已写入：新增 {res['inserted']} / 更新 {res['updated']}")
                st.session_state.pop("net_addr_rows", None)
                st.rerun()

    st.divider()
    st.markdown("#### 📤 导出")
    token = _tok()
    c1, c2, c3 = st.columns(3)
    c1.link_button("⬇️ 设备台账", f"{API_BASE}/net/devices/export?token={token}", use_container_width=True)
    c2.link_button("⬇️ IP/MAC 台账", f"{API_BASE}/net/addresses/export?token={token}", use_container_width=True)
    c3.link_button("⬇️ 整改清单", f"{API_BASE}/net/findings/export?token={token}", use_container_width=True)

    st.divider()
    st.markdown("#### ↩️ 导入批次与回滚")
    batches = _get("/net/import-batches") or []
    if batches:
        st.dataframe(pd.DataFrame([{
            "批次": b["id"], "类型": b["kind"], "文件": b["file_name"],
            "新增": b["inserted"], "更新": b["updated"], "时间": b["created_at"],
            "可回滚": "✅" if b["rollbackable"] else "—",
        } for b in batches]), use_container_width=True, hide_index=True)
        blabels = {f"#{b['id']}｜{b['kind']}｜{b['file_name']}｜新增{b['inserted']}/更新{b['updated']}": b for b in batches}
        bsel = st.selectbox("选择要回滚的批次", list(blabels.keys()), key="net_batch_sel")
        bcur = blabels[bsel]
        confirm = st.checkbox("我确认回滚该批次（新增的记录会删除，更新的字段会恢复原值）", key="net_batch_confirm")
        if st.button("回滚该批次", disabled=not confirm or not bcur["rollbackable"], key="net_batch_rollback"):
            res = _post(f"/net/import-batches/{bcur['id']}/rollback")
            if res:
                st.success(f"已回滚：删除 {res.get('deleted', 0)} 条，恢复 {res.get('restored', 0)} 条")
                st.rerun()
        if not bcur["rollbackable"]:
            st.caption("旧批次未保存快照，无法自动回滚。")
    else:
        st.caption("暂无导入批次。")

    st.divider()
    st.markdown("#### 🕘 变更日志")
    ch = _get("/net/changes", limit=300) or []
    if ch:
        st.dataframe(pd.DataFrame([{
            "时间": x["ts"], "对象": f"{x['entity_type']}｜{x['label']}", "字段": x["field"],
            "原值": (x["old"] or "")[:50], "新值": (x["new"] or "")[:50],
            "原因": x["reason"], "操作人": x["operator"],
        } for x in ch]), use_container_width=True, hide_index=True)
    else:
        st.caption("暂无变更")


# ---------------------------------------------------------------- 入口
def render_net_page():
    st.header("🛰️ 网络通讯台账")
    st.caption("站点 · 定级系统 · 设备 · IP/MAC · 拓扑图 · 等保资料｜本地规则识别，不调用 AI")

    sites = _get("/net/sites") or []
    systems = _get("/net/systems") or []
    if not sites:
        st.error("后端未返回站点数据，请确认后端已重启并完成初始化。")
        return

    tabs = st.tabs(["总览", "站点与系统", "设备台账", "IP/MAC 台账", "数据质量", "拓扑图", "等保资料", "导入导出"])
    with tabs[0]:
        _tab_overview(sites, systems)
    with tabs[1]:
        _tab_sites(sites, systems)
    with tabs[2]:
        _tab_devices(sites, systems)
    with tabs[3]:
        _tab_addresses(sites)
    with tabs[4]:
        _tab_quality(sites, systems)
    with tabs[5]:
        _tab_topologies(sites, systems)
    with tabs[6]:
        _tab_reports(sites, systems)
    with tabs[7]:
        _tab_io(sites, systems)
