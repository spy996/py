"""
研发知识智能管理平台 · 共享常量
前后端（main.py / app.py）共同引用的阶段枚举与金额口径定义，禁止在各处硬编码。
"""

# 项目阶段枚举（顺序即进度条展示顺序）
PROJECT_STAGES = ["项目立项", "项目投决", "项目实施", "项目验收"]

# 合同金额人工覆盖口径（project_contract_amount_override.kind）
AMOUNT_KINDS = ["estimate", "settlement", "income"]

# 金额口径中文标签
AMOUNT_KIND_LABELS = {
    "estimate": "合同暂估额",
    "settlement": "合同结算金额",
    "income": "实际到账流水",
}
