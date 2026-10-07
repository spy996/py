"""
研发知识智能管理平台 · 前端纯函数组件
供 Streamlit 页面复用的无状态工具：时段筛选、金额格式化、阶段进度渲染。
"""
import streamlit as st
from datetime import date, timedelta

from constants import PROJECT_STAGES


def fmt_money(value) -> str:
    """金额格式化：保留两位、千分位，空值/非法值兜底显示 ¥0.00。"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"¥{v:,.2f}"


def time_filter_widget(key_prefix: str = "", label: str = "时段筛选"):
    """通用时段筛选组件：返回 (start, end) 两个 YYYY-MM-DD 字符串；「全部时间」返回 (None, None)。

    通过 key_prefix 区分不同页面/标签页的控件实例，避免 Streamlit 控件 key 冲突。
    """
    presets = ["全部时间", "近 30 天", "近 90 天", "今年", "去年", "自定义"]
    sel = st.selectbox(label, presets, key=f"{key_prefix}_time_preset")
    today = date.today()

    if sel == "全部时间":
        return None, None

    if sel == "近 30 天":
        start, end = today - timedelta(days=30), today
    elif sel == "近 90 天":
        start, end = today - timedelta(days=90), today
    elif sel == "今年":
        start, end = date(today.year, 1, 1), today
    elif sel == "去年":
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    else:  # 自定义
        c1, c2 = st.columns(2)
        start = c1.date_input("开始日期", value=today - timedelta(days=365), key=f"{key_prefix}_start")
        end = c2.date_input("结束日期", value=today, key=f"{key_prefix}_end")

    # Streamlit date_input 可能返回 datetime，统一转 date
    if hasattr(start, "date") and not isinstance(start, date):
        start = start.date()
    if hasattr(end, "date") and not isinstance(end, date):
        end = end.date()

    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def stage_progress(stages: list, current_stage: str) -> str:
    """渲染 4 阶段横向进度条（纯 HTML），高亮当前阶段并展示各阶段资料数。

    stages: [{stage, file_count}, ...]，顺序按 PROJECT_STAGES。
    current_stage: 当前阶段中文名。
    """
    if current_stage not in PROJECT_STAGES:
        current_stage = PROJECT_STAGES[0]
    cur_idx = PROJECT_STAGES.index(current_stage)
    # 补齐阶段顺序（后端 stages 已按 PROJECT_STAGES 顺序，这里再兜底）
    stage_map = {s.get("stage"): s.get("file_count", 0) for s in (stages or [])}

    cells = []
    for i, s in enumerate(PROJECT_STAGES):
        count = stage_map.get(s, 0)
        if i < cur_idx:
            icon, border, bg = "✅", "#16a34a", "#f0fdf4"
        elif i == cur_idx:
            icon, border, bg = "▶️", "#1f5fa8", "#eaf2fb"
        else:
            icon, border, bg = "⚪", "#e5eaf0", "#ffffff"
        cells.append(
            f'<div style="flex:1;border:2px solid {border};border-radius:10px;'
            f'padding:10px 6px;text-align:center;background:{bg};margin:0 4px;">'
            f'<div style="font-weight:700;color:#16324f;">{icon} {s}</div>'
            f'<div style="color:#667085;font-size:0.85em;">资料 {count} 份</div></div>'
        )
    return f'<div style="display:flex;align-items:stretch;">{"".join(cells)}</div>'
