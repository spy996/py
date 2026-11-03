"""经费管理 v2 页面：年度逐项核对 / 明细台账 / 年度矩阵 / 合同分摊 / 财务单据 / 风险预警。

设计目标：
1. 逐项比对：合同分摊 ↔ 财务单据 ↔ 经费明细 ↔ 年度归属，每个合同/项目一行差异；
2. 一键化：一键核对、一键修复（年度归属、分类、合同关联、单据补录）；
3. 确保每年研发投入正确：以「支出类经费明细」为研发投入口径，区分「有单据支撑 / 待补单据」。
"""
from __future__ import annotations

import html
import os
from datetime import date, datetime

import pandas as pd
import requests
import streamlit as st

from ui_common import fmt_money

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

CATEGORY_ALL = "全部"
ALERT_CATEGORIES = {"合同超期", "合同临期", "逾期付款", "节点待办", "质保金到期", "延期", "超额"}
AMOUNT_KINDS = {"estimate": "合同暂估额", "settlement": "合同结算额", "income": "实际到账流水"}


# ============ 基础请求 ============
def _request(method: str, path: str, **kwargs):
    # 注意：app.py 会把 requests.get/post/put/delete 替换成带鉴权头的 _session 版本，
    # 这里必须走这四个方法，不能用 requests.request，否则不会带上 Authorization。
    fn = {"GET": requests.get, "POST": requests.post, "PUT": requests.put,
          "DELETE": requests.delete}.get(method.upper())
    if fn is None:
        st.error(f"不支持的请求方法：{method}")
        return None
    try:
        r = fn(f"{API_BASE}{path}", timeout=180, **kwargs)
    except Exception as e:
        st.error(f"请求失败：{e}")
        return None
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        st.error(f"操作失败：{detail}")
        return None
    return r


def _get(path: str, **params):
    r = _request("GET", path, params={k: v for k, v in params.items() if v is not None})
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _post(path: str, payload=None, files=None, data=None):
    r = _request("POST", path, json=payload if files is None and data is None else None,
                 files=files, data=data)
    return r


def _put(path: str, payload):
    return _request("PUT", path, json=payload)


def _delete(path: str):
    return _request("DELETE", path)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _year_now():
    return date.today().year


def _project_label(p):
    flag = "　✅ 已完结" if int(p.get("completed") or 0) else ""
    return f"{p.get('name', '')}（ID:{p.get('id')}）{flag}"


def _project_options(meta, include_completed=True):
    out = {}
    for p in meta.get("projects", []):
        if not include_completed and int(p.get("completed") or 0):
            continue
        out[_project_label(p)] = p["id"]
    return out


def _contract_label(c, with_project=True):
    name = c.get("name") or c.get("contract_name") or c.get("original_name") or f"合同#{c.get('id')}"
    no = c.get("contract_no") or ""
    label = f"#{c.get('id')}｜{name}"
    if no:
        label += f"｜{no}"
    if with_project and c.get("project_name"):
        label = f"{c['project_name']}｜{label}"
    if c.get("is_shared"):
        label += "｜共享"
    return label


def _meta_maps(meta):
    pmap = {p["id"]: p for p in meta.get("projects", [])}
    cmap = {c["id"]: c for c in meta.get("contracts", [])}
    return pmap, cmap


def _contracts_for_project(meta, project_id):
    if project_id is None:
        return []
    out = []
    for c in meta.get("contracts", []):
        if any(int(a.get("project_id") or 0) == int(project_id) for a in c.get("allocations", [])):
            out.append(c)
    return out


def _share_for_project(contract, project_id):
    for a in contract.get("allocations", []):
        if int(a.get("project_id") or 0) == int(project_id):
            return _num(a.get("amount"))
    return 0.0


def _clear_fund_form():
    for k in ("fund_form_open", "fund_form_data", "fund_form_mode", "fund_form_id"):
        st.session_state.pop(k, None)


def _open_fund_form(mode="new", entry=None):
    st.session_state["fund_form_token"] = int(st.session_state.get("fund_form_token", 0)) + 1
    st.session_state["fund_form_open"] = True
    st.session_state["fund_form_mode"] = mode
    st.session_state["fund_form_data"] = dict(entry or {})
    st.session_state["fund_form_id"] = (entry or {}).get("id") if entry else None
    st.rerun()


# ============ 页面 ============
def render_funding_page():
    st.header("💰 经费管理")
    st.caption("以「年度逐项核对」为核心：合同分摊 → 财务单据 → 经费明细 → 年度归属；一键找出差异，确保每年研发投入正确。")

    meta = _get("/funding/meta")
    if not meta:
        st.warning("无法获取经费元数据，请确认后端已启动并已加载最新代码。")
        return

    years = [int(y) for y in meta.get("years", [])]
    if _year_now() not in years:
        years.append(_year_now())
    years = sorted(set(years), reverse=True)
    c0, c1 = st.columns([1, 3])
    year = c0.selectbox("📅 核对年度", years, index=years.index(_year_now()) if _year_now() in years else 0,
                        key="fund_v2_year")
    c1.caption("研发投入口径：当年「支出」类经费明细合计；有单据支撑 = 与已确认财务单据按「合同+项目+金额」逐项匹配成功。")

    tabs = st.tabs([
        "🎯 年度核对", "📒 经费明细", "📅 年度矩阵",
        "🤝 合同分摊", "🧾 财务单据", "⚠️ 风险预警", "⚙️ 口径设置",
    ])
    with tabs[0]:
        _render_reconcile(meta, year)
    with tabs[1]:
        _render_ledger(meta, year)
    with tabs[2]:
        _render_matrix(meta, year)
    with tabs[3]:
        _render_shares(meta)
    with tabs[4]:
        _render_documents(meta, year)
    with tabs[5]:
        _render_alerts(meta)
    with tabs[6]:
        _render_settings()


# ============ 1. 年度核对 ============
def _render_reconcile(meta, year):
    st.subheader("🎯 年度研发投入逐项核对")
    st.caption("规则引擎自动比对，不调用 AI：合同分摊 → 财务单据 → 经费明细 → 年度归属。结果可复现、可导出。")

    pmap, _cmap = _meta_maps(meta)
    proj_opts = {"（全部项目）": None}
    proj_opts.update(_project_options(meta))
    c1, c2, c3 = st.columns([2.2, 1, 1])
    sel_proj = c1.selectbox("核对范围", list(proj_opts.keys()), key="fund_rec_proj")
    pid = proj_opts[sel_proj]
    show_empty = c2.checkbox("显示无数据项目", value=False, key="fund_rec_show_empty")
    hide_completed = c3.checkbox("隐藏已完结项目", value=False, key="fund_rec_hide_completed")

    data = _get("/funding/reconcile", year=year, project_id=pid)
    if not data:
        st.warning("核对失败：请确认后端已加载最新代码。")
        return
    if st.session_state.get("fund_recon_msg"):
        st.success(st.session_state.pop("fund_recon_msg"))
    if st.button("🔍 一键智能核对", type="primary", key="fund_recon_run"):
        with st.spinner("正在逐项比对合同分摊、财务单据、经费明细与年度归属……"):
            fresh = _get("/funding/reconcile", year=year, project_id=pid)
        if fresh:
            s = fresh.get("summary", {})
            st.session_state["fund_recon_msg"] = (
                f"{year} 年核对完成：研发投入 {fmt_money(s.get('total_expense'))}，"
                f"有单据 {fmt_money(s.get('total_verified'))}，待补 {fmt_money(s.get('total_unsupported'))}，"
                f"问题 {s.get('issue_count', 0)} 条（可一键修复 {s.get('auto_fixable', 0)} 条）。"
            )
            st.rerun()
    summary = data.get("summary", {})
    issues = data.get("issues", [])
    annual_rows = data.get("annual_rows", [])
    item_rows = data.get("item_rows", [])

    # KPI
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("当年研发投入（支出）", fmt_money(summary.get("total_expense", 0)))
    k2.metric("有单据支撑", fmt_money(summary.get("total_verified", 0)))
    k3.metric("待补单据", fmt_money(summary.get("total_unsupported", 0)))
    k4.metric("台账 vs 单据差异", fmt_money(summary.get("diff", 0)))
    k5.metric("项目数", summary.get("project_count", 0))
    k6.metric("问题 / 可一键修复", f"{summary.get('issue_count', 0)} / {summary.get('auto_fixable', 0)}")

    status = summary.get("status", "—")
    if status == "通过":
        st.success(f"✅ {year} 年核对通过：台账与单据逐项一致，未发现需要处理的问题。")
    elif summary.get("danger_count", 0):
        st.error(f"🔴 {year} 年核对发现 {summary.get('danger_count')} 个高风险问题、"
                 f"{summary.get('warning_count')} 个提醒，请优先处理「单据未入账」等红色项。")
    else:
        st.warning(f"🟡 {year} 年核对完成：{summary.get('warning_count')} 个提醒、"
                   f"{summary.get('info_count')} 个提示项；建议先处理「待关联合同/待补单据」。")

    stats = _get("/fundings/stats") or {}
    by_exp = stats.get("by_year_expense") or {}
    if by_exp:
        with st.expander("📈 历年研发投入趋势（支出类明细）", expanded=False):
            ys = sorted(by_exp.keys())[-8:]
            chart_df = pd.DataFrame({"年度": ys, "研发投入": [by_exp[y] for y in ys]}).set_index("年度")
            st.bar_chart(chart_df, height=220)
            st.caption("口径：各年度「支出」类经费明细合计；用于快速检查年度间是否存在异常跳变或漏记。")

    with st.expander("🧹 一键智能修复（只处理可自动判定的问题）", expanded=bool(summary.get("auto_fixable"))):
        st.caption("建议顺序：先「年度归属/分类」，再「按单据关联合同」，最后「单据补录」。每一步都会写入审计日志。")
        f1, f2, f3, f4 = st.columns(4)
        do_period = f1.checkbox("修正年度/月份", value=True, key="fund_fix_period")
        do_class = f2.checkbox("补分类/来源", value=True, key="fund_fix_class")
        do_link = f3.checkbox("按单据关联合同", value=True, key="fund_fix_link")
        do_create = f4.checkbox("单据补录台账", value=True, key="fund_fix_create",
                                help="为「已确认但台账中没有」的财务单据生成一条支出明细；不会重复补录已有金额。")
        if st.button("🧹 执行一键修复", type="primary", key="fund_fix_btn"):
            with st.spinner("正在修复……"):
                r = _post("/funding/reconcile/apply", {
                    "year": year, "project_id": pid,
                    "fix_period": do_period, "fill_classification": do_class,
                    "link_contract_from_docs": do_link, "create_entries_from_docs": do_create,
                })
            if r is not None:
                st.success(r.json().get("message", "已执行"))
                st.rerun()

    # 导出
    if annual_rows:
        export = pd.DataFrame(annual_rows).rename(columns={
            "project_name": "项目", "completed": "已完结", "contract_amount": "合同分摊",
            "budget": "预算", "income": "到账", "expense": "支出（研发投入）",
            "verified_expense": "有单据支撑", "unsupported_expense": "待补单据",
            "doc_effective": "单据实际", "diff": "差异", "status": "结论",
        })
        st.download_button("⬇️ 导出年度核对结果（CSV）", export.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"经费年度核对_{year}.csv", mime="text/csv",
                           key="fund_rec_export")

    view = st.radio("查看", ["📊 项目年度汇总", "🔍 合同逐项比对", "🧾 问题清单"],
                    horizontal=True, key="fund_rec_view")

    if view.startswith("📊"):
        rows = annual_rows
        if hide_completed:
            rows = [a for a in rows if not a.get("completed")]
        if not show_empty:
            rows = [a for a in rows if (a.get("expense") or a.get("doc_effective") or a.get("contract_amount")
                                        or a.get("income") or a.get("budget"))]
        if not rows:
            st.info("该年度暂无经费数据。可到「经费明细」新增，或在「财务单据」上传发票/结算单/暂估单。")
        else:
            df = pd.DataFrame([{
                "项目": a.get("project_name"),
                "状态": "✅ 已完结" if a.get("completed") else "进行中",
                "合同分摊": a.get("contract_amount"),
                "预算": a.get("budget"),
                "到账": a.get("income"),
                "支出（研发投入）": a.get("expense"),
                "有单据支撑": a.get("verified_expense"),
                "待补单据": a.get("unsupported_expense"),
                "单据实际": a.get("doc_effective"),
                "差异": a.get("diff"),
                "结论": a.get("status"),
            } for a in rows])
            st.dataframe(
                df, width="stretch", hide_index=True,
                column_config={
                    "合同分摊": st.column_config.NumberColumn(format="%.2f"),
                    "预算": st.column_config.NumberColumn(format="%.2f"),
                    "到账": st.column_config.NumberColumn(format="%.2f"),
                    "支出（研发投入）": st.column_config.NumberColumn(format="%.2f"),
                    "有单据支撑": st.column_config.NumberColumn(format="%.2f"),
                    "待补单据": st.column_config.NumberColumn(format="%.2f"),
                    "单据实际": st.column_config.NumberColumn(format="%.2f"),
                    "差异": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            st.caption(f"合计：研发投入 {fmt_money(summary.get('total_expense'))} ｜ "
                       f"有单据 {fmt_money(summary.get('total_verified'))} ｜ "
                       f"待补 {fmt_money(summary.get('total_unsupported'))} ｜ "
                       f"差异 {fmt_money(summary.get('diff'))}")

    elif view.startswith("🔍"):
        only_diff = st.checkbox("只看有金额/差异的合同", value=True, key="fund_rec_only_diff")
        rows = item_rows
        if only_diff:
            rows = [x for x in rows if (x.get("ledger_expense") or x.get("doc_effective")
                                        or x.get("pending_link") or abs(_num(x.get("diff"))) > 0.01)]
        if hide_completed:
            rows = [x for x in rows if not x.get("completed")]
        if not rows:
            st.info("暂无可比对的合同/台账数据。")
        else:
            df = pd.DataFrame([{
                "项目": x.get("project_name"),
                "合同": x.get("contract_name"),
                "合同编号": x.get("contract_no"),
                "合同总额": x.get("contract_total"),
                "本项目分摊": x.get("share_amount"),
                "暂估": x.get("estimate"),
                "结算": x.get("settlement"),
                "发票": x.get("invoice"),
                "单据实际": x.get("doc_effective"),
                "台账支出": x.get("ledger_expense"),
                "待关联金额": x.get("pending_link"),
                "差异": x.get("diff"),
                "状态": x.get("status"),
            } for x in rows])
            st.dataframe(
                df, width="stretch", hide_index=True,
                column_config={k: st.column_config.NumberColumn(format="%.2f") for k in
                               ["合同总额", "本项目分摊", "暂估", "结算", "发票", "单据实际", "台账支出", "待关联金额", "差异"]},
            )
            st.caption("差异 = 台账支出 − 单据实际。正数表示台账多记，负数表示台账少记；"
                       "「待关联合同」表示台账金额已与单据匹配，但明细还没挂合同，可在上方一键修复。")

    else:
        if not issues:
            st.success("🎉 没有发现问题项。")
        else:
            lv = st.multiselect("级别", ["danger", "warning", "info"], default=["danger", "warning", "info"],
                                format_func=lambda x: {"danger": "🔴 高风险", "warning": "🟡 提醒", "info": "🔵 提示"}.get(x, x),
                                key="fund_issue_levels")
            codes = sorted({i.get("code") for i in issues})
            code_sel = st.multiselect("问题类型", codes, default=codes, key="fund_issue_codes")
            shown = [i for i in issues if i.get("level") in lv and i.get("code") in code_sel]
            st.caption(f"共 {len(shown)} 条（全部 {len(issues)} 条）")
            for i in shown:
                color = {"danger": "#e53935", "warning": "#f59e0b", "info": "#3b82f6"}.get(i.get("level"), "#3b82f6")
                icon = {"danger": "🔴", "warning": "🟡", "info": "🔵"}.get(i.get("level"), "🔵")
                fix = "　<span style='color:#16a34a'>[可一键修复]</span>" if i.get("auto_fixable") else ""
                st.markdown(
                    f'<div style="border-left:4px solid {color};padding:8px 12px;margin:6px 0;'
                    f'background:#f7f8fa;border-radius:4px;">'
                    f'{icon} <b>{html.escape(i.get("title", ""))}</b>'
                    f'　<span style="color:#9aa0a6;font-size:0.85em;">{html.escape(i.get("code", ""))}</span>{fix}<br>'
                    f'<span style="color:#344054;font-size:0.92em;">{html.escape(i.get("detail", ""))}</span><br>'
                    f'<span style="color:#667085;font-size:0.86em;">建议：{html.escape(i.get("suggestion", ""))}</span></div>',
                    unsafe_allow_html=True,
                )


# ============ 2. 经费明细 ============
def _render_ledger(meta, year):
    st.subheader("📒 经费明细台账")
    st.caption("一行 = 一笔经费；支持按年度/项目/合同/类型/科目/来源/关键词查询，选中行可修改或删除。")

    pmap, cmap = _meta_maps(meta)
    proj_opts = {"（全部项目）": None}
    proj_opts.update(_project_options(meta))
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    sel_proj = c1.selectbox("项目", list(proj_opts.keys()), key="fund_led_proj")
    pid = proj_opts[sel_proj]
    ftype = c2.selectbox("类型", ["全部", "支出", "到账", "预算"], key="fund_led_type")
    cat = c3.selectbox("科目", ["全部"] + meta.get("categories", []) + ["未分类"], key="fund_led_cat")
    src = c4.selectbox("来源", ["全部"] + meta.get("sources", []) + ["未分类"], key="fund_led_src")

    c5, c6, c7, c8 = st.columns([2, 1, 1, 1])
    kw = c5.text_input("关键词（摘要/备注/凭证号）", key="fund_led_kw")
    status = c6.selectbox("状态", ["全部", "已确认", "待确认"], key="fund_led_status")
    date_from = c7.text_input("开始日期", placeholder="YYYY-MM-DD", key="fund_led_from")
    date_to = c8.text_input("结束日期", placeholder="YYYY-MM-DD", key="fund_led_to")
    include_completed = st.checkbox("包含已完结项目", value=True, key="fund_led_inc_completed")

    items = _get("/funding/entries", year=year, project_id=pid,
                 fund_type=None if ftype == "全部" else ftype,
                 category=None if cat == "全部" else cat,
                 source=None if src == "全部" else src,
                 status=None if status == "全部" else status,
                 keyword=kw or None, date_from=date_from or None, date_to=date_to or None,
                 include_completed=include_completed, limit=1000)
    if not items:
        st.warning("查询失败。")
        return
    rows = items.get("items", [])
    summary = items.get("summary", {})
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("记录数", summary.get("count", 0))
    m2.metric("合计", fmt_money(summary.get("amount", 0)))
    m3.metric("预算", fmt_money(summary.get("budget", 0)))
    m4.metric("到账", fmt_money(summary.get("income", 0)))
    m5.metric("支出", fmt_money(summary.get("expense", 0)))

    b1, b2, b3, b4 = st.columns([1, 1, 1, 2])
    if b1.button("➕ 新增明细", type="primary", key="fund_led_new"):
        _open_fund_form("new", {"year": year, "project_id": pid})
    sel_id = st.session_state.get("fund_selected_id")
    sel_entry = next((x for x in rows if x.get("id") == sel_id), None)
    if b2.button("✏️ 编辑选中", disabled=sel_entry is None, key="fund_led_edit"):
        _open_fund_form("edit", sel_entry)
    if b3.button("🗑️ 删除选中", disabled=sel_entry is None, key="fund_led_del"):
        st.session_state["fund_confirm_del"] = sel_id

    if st.session_state.get("fund_confirm_del"):
        did = st.session_state["fund_confirm_del"]
        target = next((x for x in rows if x.get("id") == did), None)
        st.warning(f"确定删除经费明细「{target.get('item_name') if target else did}」吗？删除后不可恢复。")
        dc1, dc2 = st.columns(2)
        if dc1.button("✅ 确认删除", key="fund_led_del_yes"):
            if _delete(f"/funding/entries/{did}") is not None:
                st.session_state.pop("fund_confirm_del", None)
                st.session_state.pop("fund_selected_id", None)
                st.rerun()
        if dc2.button("❌ 取消", key="fund_led_del_no"):
            st.session_state.pop("fund_confirm_del", None)
            st.rerun()

    if st.session_state.get("fund_form_open"):
        _render_fund_form(meta, year, pmap, cmap)

    if not rows:
        st.info("该筛选条件下没有经费明细。点击「➕ 新增明细」录入，或在「财务单据」上传后一键补录。")
        return

    df = pd.DataFrame([{
        "ID": r.get("id"),
        "日期": r.get("fund_date") or "",
        "年度": r.get("year"),
        "月": r.get("month"),
        "项目": r.get("project_name"),
        "合同": r.get("contract_name") or "—",
        "类型": r.get("fund_type"),
        "科目": r.get("category"),
        "来源": r.get("source"),
        "金额": r.get("amount"),
        "状态": r.get("status"),
        "凭证号": r.get("voucher_no") or "",
        "摘要": r.get("item_name"),
    } for r in rows])
    event = st.dataframe(
        df, width="stretch", hide_index=True, key="fund_ledger_df",
        on_select="rerun", selection_mode="single-row",
        column_config={"金额": st.column_config.NumberColumn(format="%.2f")},
    )
    try:
        selected_rows = event.selection.rows
    except Exception:
        selected_rows = []
    if selected_rows:
        idx = selected_rows[0]
        if 0 <= idx < len(rows):
            st.session_state["fund_selected_id"] = rows[idx]["id"]
            st.caption(f"已选中：#{rows[idx]['id']}「{rows[idx]['item_name']}」；可点上方「编辑选中」或「删除选中」。")

    # 附件（针对当前编辑中的明细）
    edit_id = st.session_state.get("fund_form_id")
    if edit_id and st.session_state.get("fund_form_mode") == "edit":
        _render_entry_attachments(edit_id)


def _render_fund_form(meta, year, pmap, cmap):
    data = st.session_state.get("fund_form_data", {})
    token = st.session_state.get("fund_form_token", 0)
    mode = st.session_state.get("fund_form_mode", "new")
    st.divider()
    st.markdown(f"**{'✏️ 修改经费明细' if mode == 'edit' else '➕ 新增经费明细'}**")

    proj_opts = {"（平台级 / 不关联项目）": None}
    proj_opts.update(_project_options(meta))
    labels = list(proj_opts.keys())
    cur_pid = data.get("project_id")
    cur_label = next((l for l, v in proj_opts.items() if v == cur_pid), labels[0])
    sel_proj_label = st.selectbox("归属项目", labels, index=labels.index(cur_label),
                                  key=f"ff_proj_{token}")
    pid = proj_opts[sel_proj_label]

    contracts = _contracts_for_project(meta, pid) if pid is not None else []
    ct_opts = {"（不关联合同）": None}
    for c in contracts:
        ct_opts[_contract_label(c, with_project=False)] = c["id"]
    ct_labels = list(ct_opts.keys())
    cur_cid = data.get("contract_id")
    cur_ct_label = next((l for l, v in ct_opts.items() if v == cur_cid), ct_labels[0])
    sel_ct_label = st.selectbox("关联合同（可选）", ct_labels, index=ct_labels.index(cur_ct_label),
                                key=f"ff_contract_{token}")
    cid = ct_opts[sel_ct_label]
    if cid and pid is not None:
        c = cmap.get(cid, {})
        share = _share_for_project(c, pid)
        if share:
            st.caption(f"该合同在本项目的分摊额：{fmt_money(share)}；合同总额 {fmt_money(c.get('total'))}。")
        else:
            st.warning("该合同尚未分摊到本项目，保存前请先到「合同分摊」配置。")

    year_opts = sorted(set([int(year)] + [int(y) for y in meta.get("years", [])]), reverse=True)
    cy = data.get("year") or year
    cm = data.get("month")
    with st.form(f"fund_entry_form_{token}"):
        c1, c2, c3, c4 = st.columns(4)
        sel_year = c1.selectbox("年度", year_opts, index=year_opts.index(int(cy)) if int(cy) in year_opts else 0,
                                key=f"fe_year_{token}")
        month_opts = ["未指定"] + [f"{m} 月" for m in range(1, 13)]
        month_idx = (int(cm) if cm and 1 <= int(cm) <= 12 else 0)
        sel_month = c2.selectbox("月份", month_opts, index=month_idx, key=f"fe_month_{token}")
        ftype = c3.selectbox("类型", ["支出", "到账", "预算"],
                             index=["支出", "到账", "预算"].index(data.get("fund_type") or "支出"),
                             key=f"fe_type_{token}")
        status = c4.selectbox("状态", ["已确认", "待确认"],
                              index=0 if (data.get("status") or "已确认") == "已确认" else 1,
                              key=f"fe_status_{token}")

        c5, c6, c7, c8 = st.columns(4)
        amount = c5.number_input("金额（元）", min_value=0.0, step=1000.0, format="%.2f",
                                 value=_num(data.get("amount")), key=f"fe_amount_{token}")
        fund_date = c6.text_input("发生日期", value=data.get("fund_date") or "", placeholder="YYYY-MM-DD",
                                  key=f"fe_date_{token}")
        category = c7.selectbox("经费科目", meta.get("categories", []) + ["未分类"],
                                index=((meta.get("categories", []) + ["未分类"]).index(data.get("category"))
                                       if data.get("category") in (meta.get("categories", []) + ["未分类"]) else 0),
                                key=f"fe_cat_{token}")
        source = c8.selectbox("经费来源", meta.get("sources", []) + ["未分类"],
                              index=((meta.get("sources", []) + ["未分类"]).index(data.get("source"))
                                     if data.get("source") in (meta.get("sources", []) + ["未分类"]) else 0),
                              key=f"fe_src_{token}")

        c9, c10 = st.columns([1, 2])
        voucher_no = c9.text_input("凭证号 / 单据号", value=data.get("voucher_no") or "", key=f"fe_voucher_{token}")
        item_name = c10.text_input("摘要 / 名称", value=data.get("item_name") or "",
                                   placeholder="如：暂估单、设备费付款、省拨经费到账", key=f"fe_item_{token}")
        remark = st.text_area("备注", value=data.get("remark") or "", height=68, key=f"fe_remark_{token}")
        submitted = st.form_submit_button("💾 保存", type="primary", width="stretch")

    cxl = st.button("取消", key=f"ff_cancel_{token}")
    if cxl:
        _clear_fund_form()
        st.rerun()

    if submitted:
        if not (item_name or "").strip():
            st.error("请填写摘要/名称")
        elif _num(amount) <= 0:
            st.error("金额需大于 0")
        else:
            payload = {
                "project_id": pid,
                "contract_id": cid,
                "item_name": item_name.strip(),
                "fund_type": ftype,
                "amount": _num(amount),
                "fund_date": (fund_date or "").strip() or None,
                "year": int(sel_year),
                "month": None if sel_month == "未指定" else int(sel_month.split()[0]),
                "category": category,
                "source": source,
                "voucher_no": (voucher_no or "").strip() or None,
                "status": status,
                "remark": (remark or "").strip() or None,
            }
            if mode == "edit":
                r = _put(f"/funding/entries/{st.session_state.get('fund_form_id')}", payload)
            else:
                r = _post("/funding/entries", payload)
            if r is not None:
                st.success("已保存")
                _clear_fund_form()
                st.rerun()


def _render_entry_attachments(entry_id):
    d = _get(f"/funding/entries/{entry_id}")
    if not d:
        return
    atts = d.get("attachments", [])
    with st.expander(f"📎 附件（{len(atts)} 个）", expanded=False):
        tok = st.session_state.get("token", "")
        for a in atts:
            ac1, ac2 = st.columns([5, 1])
            ac1.markdown(f"📄 `{a.get('file_name')}`　[⬇️ 下载]({API_BASE}{a.get('download_url')}?token={tok})")
            if ac2.button("🗑️", key=f"fund_att_del_{a['id']}", width="stretch"):
                if _delete(f"/funding/attachments/{a['id']}") is not None:
                    st.rerun()
        ups = st.file_uploader("上传附件（可多选）", accept_multiple_files=True,
                               key=f"fund_att_up_{entry_id}")
        if st.button("⬆️ 上传附件", key=f"fund_att_btn_{entry_id}", width="stretch"):
            if not ups:
                st.warning("请先选择文件")
            else:
                files = [("files", (f.name, f.getvalue())) for f in ups]
                r = _post(f"/funding/entries/{entry_id}/attachments", files=files)
                if r is not None:
                    st.success(f"已上传 {len(r.json().get('uploaded', []))} 个附件")
                    st.rerun()


# ============ 3. 年度矩阵 ============
def _render_matrix(meta, year):
    st.subheader("📅 年度经费矩阵（项目 × 月份）")
    st.caption("矩阵是经费明细的透视视图，不再单独维护单元格；点选下方项目/月份可查看并补录该月明细。")

    c1, c2, c3 = st.columns([1, 1, 2])
    caliber = c1.selectbox("统计口径", ["全部", "支出", "到账", "预算"], key="fund_mtx_type")
    include_completed = c2.checkbox("包含已完结项目", value=True, key="fund_mtx_inc_completed")
    data = _get("/funding/pivot", year=year, fund_type=None if caliber == "全部" else caliber,
                include_completed=include_completed)
    if not data:
        st.warning("获取年度矩阵失败。")
        return
    summary = data.get("summary", {})
    c3.caption(f"合计 {fmt_money(summary.get('total', 0))} ｜ {summary.get('count', 0)} 笔")

    rows = data.get("rows", [])
    cells = data.get("cells", {})
    atts = data.get("attachments", {})
    if not rows:
        st.info("该年度暂无纳入矩阵的项目。可在下方「项目行管理」添加。")
    else:
        matrix = []
        for r in rows:
            pid = r["project_id"]
            vals = [r["project_name"] + ("　✅已完结" if r.get("completed") else "")]
            total = 0.0
            for m in range(1, 13):
                cell = cells.get(f"{pid}-{m}") or {}
                amt = _num(cell.get("amount"))
                n = int(cell.get("count") or 0)
                if amt or n:
                    total += amt
                    s = f"{amt:,.2f}" if amt else ""
                    if n > 1:
                        s += f"（{n}笔）"
                    an = int(atts.get(f"{pid}-{m}") or 0)
                    if an:
                        s += f" 📎{an}"
                    vals.append(s)
                else:
                    vals.append("")
            vals.append(f"{total:,.2f}" if total else "")
            matrix.append(vals)
        df = pd.DataFrame(matrix, columns=["项目"] + [f"{m}月" for m in range(1, 13)] + ["合计"])
        st.dataframe(df, width="stretch", hide_index=True)

        # 合同年度金额
        with st.expander("💴 年度合同金额（暂估 / 结算 / 到账，可人工修正）", expanded=False):
            _render_contract_amounts(meta, year, data, rows)

        # 单元格明细
        st.markdown("**🔎 单元格明细 / 快速补录**")
        pmap, cmap = _meta_maps(meta)
        row_map = {r["project_id"]: r for r in rows}
        e1, e2 = st.columns(2)
        sel_pid = e1.selectbox("项目", [r["project_id"] for r in rows],
                               format_func=lambda x: row_map[x]["project_name"], key="fund_cell_proj")
        sel_month = e2.selectbox("月份", list(range(1, 13)), format_func=lambda m: f"{m} 月",
                                 key="fund_cell_month")
        detail = _get("/funding/entries", year=year, project_id=sel_pid, limit=1000)
        if detail:
            cell_rows = [x for x in detail.get("items", []) if x.get("month") == sel_month]
            if cell_rows:
                st.dataframe(pd.DataFrame([{
                    "日期": x.get("fund_date"), "类型": x.get("fund_type"), "科目": x.get("category"),
                    "金额": x.get("amount"), "摘要": x.get("item_name"), "合同": x.get("contract_name") or "—",
                } for x in cell_rows]), width="stretch", hide_index=True,
                    column_config={"金额": st.column_config.NumberColumn(format="%.2f")})
            else:
                st.caption("该单元格暂无明细。")
        with st.form(f"fund_cell_add_{sel_pid}_{sel_month}"):
            a1, a2, a3, a4 = st.columns(4)
            amt = a1.number_input("金额（元）", min_value=0.0, step=1000.0, format="%.2f", key=f"fca_amt_{sel_pid}_{sel_month}")
            fdate = a2.text_input("发生日期", placeholder="YYYY-MM-DD", key=f"fca_date_{sel_pid}_{sel_month}")
            cat = a3.selectbox("科目", meta.get("categories", []), key=f"fca_cat_{sel_pid}_{sel_month}")
            src = a4.selectbox("来源", meta.get("sources", []), key=f"fca_src_{sel_pid}_{sel_month}")
            item = st.text_input("摘要 / 名称", value=f"{sel_month} 月经费", key=f"fca_item_{sel_pid}_{sel_month}")
            add_cell = st.form_submit_button("➕ 新增到该单元格", width="stretch")
        if add_cell:
            if _num(amt) <= 0:
                st.error("金额需大于 0")
            else:
                r = _post("/funding/entries", {
                    "project_id": sel_pid, "item_name": item or f"{sel_month} 月经费",
                    "fund_type": "支出", "amount": _num(amt), "fund_date": fdate or None,
                    "year": year, "month": sel_month, "category": cat, "source": src,
                    "status": "已确认",
                })
                if r is not None:
                    st.success("已新增")
                    st.rerun()

    # 行管理
    with st.expander("🗂️ 项目行管理（添加 / 排序 / 删除该年整行）", expanded=False):
        existing_ids = {r["project_id"] for r in rows}
        avail = {_project_label(p): p["id"] for p in meta.get("projects", []) if p["id"] not in existing_ids}
        if avail:
            l1, l2 = st.columns([4, 1])
            sel_add = l1.selectbox("添加项目行", list(avail.keys()), key="fund_mtx_add_sel")
            if l2.button("➕ 添加", key="fund_mtx_add_btn", width="stretch"):
                r = _post("/funding-grid/rows", {"year": year, "project_id": avail[sel_add]})
                if r is not None:
                    st.rerun()
        else:
            st.caption("所有项目都已在该年度矩阵中。")
        st.divider()
        for i, r in enumerate(rows):
            cc1, cc2, cc3, cc4 = st.columns([5, 1, 1, 1])
            cc1.markdown(f"**{r['project_name']}**" + ("　✅已完结" if r.get("completed") else ""))
            if r.get("id"):
                if cc2.button("⬆️", key=f"fund_mtx_up_{r['id']}", disabled=(i == 0)):
                    _post(f"/funding-grid/rows/{r['id']}/move", {"direction": "up"})
                    st.rerun()
                if cc3.button("⬇️", key=f"fund_mtx_down_{r['id']}", disabled=(i == len(rows) - 1)):
                    _post(f"/funding-grid/rows/{r['id']}/move", {"direction": "down"})
                    st.rerun()
                if cc4.button("🗑️", key=f"fund_mtx_del_{r['id']}"):
                    st.session_state["fund_mtx_del_row"] = r["id"]
            else:
                cc2.caption("自动行")
        if st.session_state.get("fund_mtx_del_row"):
            did = st.session_state["fund_mtx_del_row"]
            st.warning("删除该行会同时删除该年度该项目的全部经费明细与附件，确定吗？")
            d1, d2 = st.columns(2)
            if d1.button("✅ 确认删除", key="fund_mtx_del_yes"):
                if _delete(f"/funding-grid/rows/{did}") is not None:
                    st.session_state.pop("fund_mtx_del_row", None)
                    st.rerun()
            if d2.button("❌ 取消", key="fund_mtx_del_no"):
                st.session_state.pop("fund_mtx_del_row", None)
                st.rerun()


def _render_contract_amounts(meta, year, data, rows):
    pmap, _cmap = _meta_maps(meta)
    ca = data.get("contract_amounts", {})
    row_map = {r["project_id"]: r for r in rows}
    labels = [r["project_id"] for r in rows]
    sel = st.selectbox("项目", labels, format_func=lambda x: row_map[x]["project_name"],
                       key="fund_ca_proj")
    entry = ca.get(str(sel), {})
    st.caption(f"合同暂估额 {fmt_money(entry.get('estimate'))}"
               f"{'（人工）' if entry.get('estimate_override') else ''} ｜ "
               f"合同结算额 {fmt_money(entry.get('settlement'))}"
               f"{'（人工）' if entry.get('settlement_override') else ''} ｜ "
               f"实际到账流水 {fmt_money(entry.get('income'))}"
               f"{'（人工）' if entry.get('income_override') else ''}")
    clist = entry.get("contracts", [])
    if clist:
        st.dataframe(pd.DataFrame([{
            "合同": c.get("contract_name"), "编号": c.get("contract_no"),
            "合同总额": c.get("amount_incl_tax"), "本项目分摊": c.get("share_amount"),
            "本年暂估": c.get("estimate"), "本年结算": c.get("settlement"),
            "共享": "是" if c.get("is_shared") else "",
        } for c in clist]), width="stretch", hide_index=True,
            column_config={k: st.column_config.NumberColumn(format="%.2f") for k in
                           ["合同总额", "本项目分摊", "本年暂估", "本年结算"]})
    else:
        st.caption("该项目暂无参与分摊的合同。")
    with st.form(f"fund_ca_form_{sel}"):
        c1, c2 = st.columns(2)
        kind = c1.selectbox("修正项", list(AMOUNT_KINDS.keys()), format_func=lambda k: AMOUNT_KINDS[k])
        val = c2.number_input("人工金额（元）", min_value=0.0, step=1000.0, format="%.2f",
                              value=_num(entry.get(kind)))
        b1, b2 = st.columns(2)
        save = b1.form_submit_button("💾 保存人工值")
        reset = b2.form_submit_button("↩️ 恢复自动值")
    if save:
        r = _post("/funding-grid/contract-amount-override",
                  {"project_id": sel, "year": year, "kind": kind, "amount": _num(val)})
        if r is not None:
            st.success("已保存")
            st.rerun()
    if reset:
        r = _post("/funding-grid/contract-amount-override",
                  {"project_id": sel, "year": year, "kind": kind, "amount": None})
        if r is not None:
            st.success("已恢复自动值")
            st.rerun()


# ============ 4. 合同分摊 ============
def _render_shares(meta):
    st.subheader("🤝 合同分摊（共享合同）")
    st.caption("一个合同可被多个项目共用：在这里按金额拆到各项目，各项目只计自己的分摊额，避免同一合同重复计入。")
    pmap, _cmap = _meta_maps(meta)
    data = _get("/funding/contracts")
    if not data:
        st.warning("获取合同分摊失败。")
        return
    items = data.get("items", [])
    shared = [x for x in items if x.get("is_shared")]
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("合同数", len(items))
    s2.metric("共享合同", len(shared))
    s3.metric("已分摊合同", len([x for x in items if x.get("raw_share_sum")]))
    s4.metric("未分摊余额合计", fmt_money(sum(max(0.0, _num(x.get("unallocated"))) for x in items)))

    only_shared = st.checkbox("只看共享合同", value=False, key="fund_share_only")
    sel_items = [x for x in items if (not only_shared or x.get("is_shared"))]
    if not sel_items:
        st.info("暂无合同。请先在「合同管理」上传合同。")
        return
    df = pd.DataFrame([{
        "ID": x.get("contract_id"),
        "合同": x.get("contract_name"),
        "编号": x.get("contract_no"),
        "合同总额": x.get("total"),
        "主项目": x.get("project_name"),
        "分摊项目数": len(x.get("allocations", [])),
        "已分摊": x.get("raw_share_sum"),
        "未分摊余额": x.get("unallocated"),
        "共享": "✅" if x.get("is_shared") else "",
    } for x in sel_items])
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={k: st.column_config.NumberColumn(format="%.2f") for k in
                                ["合同总额", "已分摊", "未分摊余额"]})

    ct_opts = {_contract_label(x): x["contract_id"] for x in sel_items}
    sel_label = st.selectbox("选择合同进行分摊编辑", list(ct_opts.keys()), key="fund_share_contract")
    cid = ct_opts[sel_label]
    payload = _get(f"/contracts/{cid}/shares")
    if not payload:
        return
    total = _num(payload.get("total"))
    st.markdown(f"**{payload.get('contract_name')}** ｜ 合同总额 {fmt_money(total)} ｜ "
                f"主项目：{payload.get('project_name') or '—'}")
    st.caption("规则：未配置分摊行时，合同 100% 归主项目；配置后按各项目金额计。"
               "分摊合计小于合同总额时，余额自动归主项目；大于总额会被拒绝。")

    proj_labels = _project_options(meta)
    label_to_pid = dict(proj_labels)
    pid_to_label = {v: k for k, v in proj_labels.items()}
    shares = payload.get("shares", [])
    if shares:
        seed = [{
            "项目": pid_to_label.get(s.get("project_id"), str(s.get("project_id"))),
            "分摊金额": _num(s.get("amount")),
            "备注": s.get("remark") or "",
        } for s in shares]
    else:
        primary = payload.get("project_id")
        seed = [{
            "项目": pid_to_label.get(primary, ""),
            "分摊金额": total,
            "备注": "默认 100% 归主项目",
        }]
    edited = st.data_editor(
        pd.DataFrame(seed, columns=["项目", "分摊金额", "备注"]),
        num_rows="dynamic", width="stretch", key=f"fund_share_editor_{cid}",
        column_config={
            "项目": st.column_config.SelectboxColumn("项目", options=list(proj_labels.keys()), required=True),
            "分摊金额": st.column_config.NumberColumn("分摊金额", min_value=0.0, format="%.2f"),
            "备注": st.column_config.TextColumn("备注"),
        },
    )
    raw_sum = round(sum(_num(x.get("分摊金额")) for x in edited.to_dict("records") if x.get("项目")), 2)
    unalloc = round(total - raw_sum, 2)
    if abs(unalloc) < 0.01:
        st.success(f"分摊合计 {fmt_money(raw_sum)} = 合同总额，已分完。")
    elif unalloc > 0:
        st.info(f"已分摊 {fmt_money(raw_sum)}，余额 {fmt_money(unalloc)} 将自动归主项目「{payload.get('project_name') or '—'}」。")
    else:
        st.error(f"分摊合计 {fmt_money(raw_sum)} 超过合同总额 {fmt_money(total)}，请调整。")
    if st.button("💾 保存分摊", type="primary", key=f"fund_share_save_{cid}"):
        rows = edited.to_dict("records")
        out, seen = [], set()
        for r in rows:
            label = r.get("项目")
            if not label or label not in label_to_pid:
                continue
            pid = label_to_pid[label]
            if pid in seen:
                st.error(f"项目「{label}」重复，请合并为一行。")
                break
            seen.add(pid)
            out.append({"project_id": pid, "amount": _num(r.get("分摊金额")), "remark": r.get("备注") or ""})
        else:
            r = _put(f"/contracts/{cid}/shares", {"shares": out})
            if r is not None:
                st.success("分摊已保存")
                st.rerun()

    st.divider()
    st.markdown("**当前计算口径**")
    st.dataframe(pd.DataFrame([{
        "项目": a.get("project_name"), "分摊金额": a.get("amount"),
    } for a in payload.get("allocations", [])]), width="stretch", hide_index=True,
        column_config={"分摊金额": st.column_config.NumberColumn(format="%.2f")})


# ============ 5. 财务单据 ============
def _render_documents(meta, year):
    st.subheader("🧾 财务单据（暂估单 / 结算单 / 发票）")
    st.caption("1 个合同 → 多张单据；共享合同的单据必须指定归属项目。确认后参与年度逐项核对。")
    pmap, cmap = _meta_maps(meta)
    contracts = meta.get("contracts", [])
    if not contracts:
        st.info("暂无合同，请先在「合同管理」上传。")
        return

    with st.expander("⬆️ 上传财务单据", expanded=False):
        mode = st.radio("上传方式", ["按项目上传（推荐）", "按合同上传"], horizontal=True, key="fund_doc_mode")
        if mode.startswith("按项目"):
            proj_opts = _project_options(meta)
            sel_proj_label = st.selectbox("项目", list(proj_opts.keys()), key="fund_doc_proj")
            pid = proj_opts[sel_proj_label]
            proj_contracts = _contracts_for_project(meta, pid)
            ct_opts = {_contract_label(c, with_project=False): c["id"] for c in proj_contracts}
            if not ct_opts:
                st.warning("该项目暂无参与分摊的合同。")
                return
            sel_cts = st.multiselect("关联合同（默认全选）", list(ct_opts.keys()), default=list(ct_opts.keys()),
                                     key="fund_doc_cts")
            doc_type = st.selectbox("单据类型", ["发票", "结算单", "暂估单"], key="fund_doc_type")
            files = st.file_uploader("选择单据文件（可多选）", accept_multiple_files=True, key="fund_doc_files")
            if st.button("⬆️ 上传并解析", type="primary", key="fund_doc_up"):
                if not files:
                    st.warning("请先选择文件")
                elif not sel_cts:
                    st.warning("请至少选择一个合同")
                else:
                    ids = [ct_opts[x] for x in sel_cts]
                    groups = {}
                    for f in files:
                        matched = None
                        for c in proj_contracts:
                            if c["id"] not in ids:
                                continue
                            if (c.get("contract_name") and c["contract_name"] in f.name) or \
                               (c.get("contract_no") and c["contract_no"] in f.name):
                                matched = c["id"]
                                break
                        groups.setdefault(matched or ids[0], []).append(f)
                    ok, errs = 0, []
                    for cid, fs in groups.items():
                        r = _post("/financial-docs",
                                  files=[("files", (f.name, f.getvalue())) for f in fs],
                                  data={"contract_id": str(cid), "doc_type": doc_type, "project_id": str(pid)})
                        if r is not None:
                            d = r.json()
                            ok += len(d.get("uploaded", []))
                            errs += [e.get("filename", "") for e in d.get("errors", [])]
                    if ok:
                        st.success(f"已上传 {ok} 份，后台正在提取金额/税额/日期")
                    if errs:
                        st.warning(f"部分失败：{'；'.join(errs)}")
                    if ok:
                        st.rerun()
        else:
            ct_opts = {_contract_label(c): c["id"] for c in contracts}
            c1, c2 = st.columns(2)
            sel_ct = c1.selectbox("关联合同", list(ct_opts.keys()), key="fund_doc_ct")
            doc_type = c2.selectbox("单据类型", ["发票", "结算单", "暂估单"], key="fund_doc_type2")
            cid = ct_opts[sel_ct]
            c = cmap.get(cid, {})
            pid = c.get("project_id")
            if c.get("is_shared"):
                proj_opts = _project_options(meta)
                default = next((l for l, v in proj_opts.items() if v in
                                [a.get("project_id") for a in c.get("allocations", [])]), list(proj_opts.keys())[0])
                sel_p = st.selectbox("归属项目（共享合同必选）", list(proj_opts.keys()),
                                     index=list(proj_opts.keys()).index(default), key="fund_doc_shared_proj")
                pid = proj_opts[sel_p]
            files = st.file_uploader("选择单据文件（可多选）", accept_multiple_files=True, key="fund_doc_files2")
            if st.button("⬆️ 上传并解析", type="primary", key="fund_doc_up2"):
                if not files:
                    st.warning("请先选择文件")
                else:
                    data = {"contract_id": str(cid), "doc_type": doc_type}
                    if pid is not None:
                        data["project_id"] = str(pid)
                    r = _post("/financial-docs",
                              files=[("files", (f.name, f.getvalue())) for f in files], data=data)
                    if r is not None:
                        d = r.json()
                        if d.get("uploaded"):
                            st.success(f"已上传 {len(d['uploaded'])} 份")
                            st.rerun()
                        else:
                            st.warning("没有单据被上传，请检查文件格式或后端日志")

    st.divider()
    f1, f2, f3, f4 = st.columns([2, 2, 1, 1])
    proj_opts = {"（全部项目）": None}
    proj_opts.update(_project_options(meta))
    sel_proj = f1.selectbox("筛选项目", list(proj_opts.keys()), key="fund_doc_filter_proj")
    pid = proj_opts[sel_proj]
    ct_filter = {"（全部合同）": None}
    ct_filter.update({_contract_label(c, with_project=False): c["id"] for c in contracts})
    sel_ct = f2.selectbox("筛选合同", list(ct_filter.keys()), key="fund_doc_filter_ct")
    cid = ct_filter[sel_ct]
    dtype = f3.selectbox("类型", ["全部", "发票", "结算单", "暂估单"], key="fund_doc_filter_type")
    confirmed = f4.selectbox("状态", ["全部", "已确认", "待确认"], key="fund_doc_filter_conf")
    docs = _get("/financial-docs", project_id=pid, contract_id=cid,
                doc_type=None if dtype == "全部" else dtype)
    if docs is None:
        st.warning("获取财务单据失败。")
        return
    if confirmed != "全部":
        docs = [d for d in docs if (int(d.get("confirmed") or 0) == 1) == (confirmed == "已确认")]
    if not docs:
        st.info("暂无符合条件的财务单据。")
        return
    m1, m2 = st.columns(2)
    m1.metric("单据数", len(docs))
    m2.metric("金额合计", fmt_money(sum(_num(d.get("amount")) for d in docs)))
    for d in docs:
        status = "✅ 已确认" if d.get("confirmed") else "🕓 待确认"
        c = cmap.get(d.get("contract_id"), {})
        pname = pmap.get(d.get("project_id"), {}).get("name") or "未指定项目"
        with st.expander(f"{status}｜{d.get('doc_type')}｜{d.get('original_name')}｜{fmt_money(d.get('amount'))}"):
            st.caption(f"项目：{pname}　｜　合同：{c.get('name') or c.get('contract_name') or '—'}")
            if d.get("remark"):
                st.caption(f"📝 {d['remark']}")
            tok = st.session_state.get("token", "")
            if d.get("download_url"):
                st.link_button("⬇️ 下载附件", f"{API_BASE}{d['download_url']}?token={tok}",
                               key=f"fund_doc_dl_{d['id']}", width="stretch")
            with st.form(f"fund_doc_edit_{d['id']}"):
                e1, e2, e3 = st.columns(3)
                amt = e1.number_input("金额（价税合计）", value=_num(d.get("amount")), step=100.0, format="%.2f")
                tax = e2.number_input("税额", value=_num(d.get("tax_amount")), step=10.0, format="%.2f")
                ddate = e3.text_input("发生日期", value=d.get("doc_date") or "", placeholder="YYYY-MM-DD")
                e4, e5, e6 = st.columns(3)
                dtype2 = e4.selectbox("单据类型", ["发票", "结算单", "暂估单"],
                                      index=["发票", "结算单", "暂估单"].index(d.get("doc_type") or "发票"))
                doc_pid = e5.selectbox("归属项目", list(proj_opts.keys()),
                                       index=next((i for i, (k, v) in enumerate(proj_opts.items())
                                                   if v == d.get("project_id")), 0))
                ct_edit_opts = {_contract_label(c, with_project=False): c["id"] for c in contracts}
                doc_ct = e6.selectbox("关联合同", list(ct_edit_opts.keys()),
                                      index=next((i for i, (k, v) in enumerate(ct_edit_opts.items())
                                                  if v == d.get("contract_id")), 0))
                save = st.form_submit_button("💾 保存校对", width="stretch")
            if save:
                r = _put(f"/financial-docs/{d['id']}", {
                    "amount": _num(amt), "tax_amount": _num(tax), "doc_date": ddate or None,
                    "doc_type": dtype2, "project_id": proj_opts[doc_pid],
                    "contract_id": ct_edit_opts[doc_ct],
                })
                if r is not None:
                    st.success("已保存")
                    st.rerun()
            b1, b2 = st.columns(2)
            if not d.get("confirmed"):
                if b1.button("✅ 确认生效", key=f"fund_doc_cf_{d['id']}"):
                    if _post(f"/financial-docs/{d['id']}/confirm") is not None:
                        st.rerun()
            if b2.button("🗑️ 删除", key=f"fund_doc_del_{d['id']}"):
                if _delete(f"/financial-docs/{d['id']}") is not None:
                    st.rerun()


# ============ 6. 风险预警 ============
def _render_alerts(meta):
    st.subheader("⚠️ 风险预警")
    st.caption("已完结项目自动不显示风险提醒；网络台账类提醒请在「网络通讯台账」查看。")
    data = _get("/funding/alerts")
    if not data:
        st.warning("获取预警失败。")
        return
    alerts = [a for a in data.get("alerts", []) if a.get("category") in ALERT_CATEGORIES]
    completed = [p for p in meta.get("projects", []) if int(p.get("completed") or 0)]
    st.caption(f"当前已完结项目 {len(completed)} 个，其合同/付款/延期/超额类提醒已自动隐藏。")

    if not alerts:
        st.success("🎉 当前没有经费/合同类风险预警。")
        return
    lv = st.multiselect("级别", ["danger", "warning", "info"], default=["danger", "warning", "info"],
                        format_func=lambda x: {"danger": "🔴 高风险", "warning": "🟡 提醒", "info": "🔵 提示"}.get(x, x),
                        key="fund_alert_levels")
    cats = sorted({a.get("category") for a in alerts})
    cat_sel = st.multiselect("分类", cats, default=cats, key="fund_alert_cats")
    shown = [a for a in alerts if a.get("level") in lv and a.get("category") in cat_sel]
    m1, m2, m3 = st.columns(3)
    m1.metric("预警数", len(shown))
    m2.metric("高风险", len([a for a in shown if a.get("level") == "danger"]))
    m3.metric("提醒", len([a for a in shown if a.get("level") == "warning"]))
    if st.button("🧹 批量清理已过期提醒", key="fund_alert_expired"):
        r = _post("/funding/alerts/dismiss-expired")
        if r is not None:
            st.success(f"已清理 {r.json().get('dismissed_count', 0)} 条")
            st.rerun()
    for a in shown:
        color = {"danger": "#e53935", "warning": "#f59e0b", "info": "#3b82f6"}.get(a.get("level"), "#3b82f6")
        icon = {"danger": "🔴", "warning": "🟡", "info": "🔵"}.get(a.get("level"), "🔵")
        c1, c2 = st.columns([9, 1])
        c1.markdown(
            f'<div style="border-left:4px solid {color};padding:8px 12px;margin:6px 0;'
            f'background:#f7f8fa;border-radius:4px;">'
            f'{icon} <b>{html.escape(a.get("category", ""))}</b> ｜ {html.escape(a.get("title", ""))}<br>'
            f'<span style="color:#5f6368;font-size:0.9em;">{html.escape(a.get("detail", ""))}</span></div>',
            unsafe_allow_html=True,
        )
        if c2.button("🔕 不再提醒", key=f"fund_alert_dismiss_{a.get('alert_key', a.get('source_id'))}"):
            r = _post("/funding/alerts/dismiss", {"alert_key": a.get("alert_key"), "category": a.get("category")})
            if r is not None:
                st.rerun()


# ============ 7. 口径设置 ============
def _render_settings():
    st.subheader("⚙️ 经费口径设置")
    cfg = _get("/config") or {}
    with st.form("fund_settings_form"):
        basis_opts = {"发票额（默认）": "invoice", "暂估额": "estimate", "三列并存": "both"}
        cur_basis = cfg.get("final_amount_basis", "invoice")
        new_basis = st.selectbox("最终填充金额口径", list(basis_opts.keys()),
                                 index=list(basis_opts.values()).index(cur_basis) if cur_basis in basis_opts.values() else 0)
        ob_opts = {"合同含税金额（默认）": "contract_incl_tax", "项目预算总额": "project_budget"}
        cur_ob = cfg.get("overrun_basis", "contract_incl_tax")
        new_ob = st.selectbox("超额判定基准", list(ob_opts.keys()),
                              index=list(ob_opts.values()).index(cur_ob) if cur_ob in ob_opts.values() else 0)
        tol = st.number_input("超额容忍比例（0.05 表示超 5% 才报警）", min_value=0.0, max_value=1.0,
                              step=0.01, value=float(cfg.get("overrun_tolerance", 0) or 0))
        submitted = st.form_submit_button("💾 保存口径", type="primary")
    if submitted:
        r = _put("/config", {"config": {
            "final_amount_basis": basis_opts[new_basis],
            "overrun_basis": ob_opts[new_ob],
            "overrun_tolerance": str(tol),
        }})
        if r is not None:
            st.success("已保存")
            st.rerun()

    st.divider()
    st.markdown(
        """
        **核对口径说明**
        - **研发投入**：当年「支出」类经费明细合计（预算、到账单独列示，不计入研发投入）。
        - **有单据支撑**：明细与财务单据按「合同 + 项目 + 年度 + 金额」逐项匹配成功；匹配不上则计入「待补单据」。
        - **合同分摊**：共享合同先拆到项目，各项目只计自己的分摊额；未分完余额自动归主项目。
        - **年度归属**：优先取明细的年度/月份；缺失时按发生日期自动推导。
        - **一键修复**：只处理规则可确定的问题（年度/月份、缺分类、单据可匹配的合同、单据补录），不覆盖人工金额。

        **经费科目**：设备费、材料费、测试化验加工费、燃料动力费、差旅费、会议费、国际合作与交流费、
        出版/文献/信息传播/知识产权事务费、劳务费、专家咨询费、管理费、其他支出。

        **经费来源**：国拨经费、省拨经费、集团拨款、企业自筹、其他。
        """
    )
