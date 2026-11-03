"""数据模型：18 个 SQLAlchemy 表 + 建表 + 旧库迁移。"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, UniqueConstraint, inspect, text
from sqlalchemy.orm import relationship

from database import Base, engine


class Project(Base):
    """项目表"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    stage = Column(String(20), nullable=True)  # 当前阶段（项目立项/项目投决/项目实施/项目验收）
    start_year = Column(Integer, nullable=True, index=True)  # 立项年份（如 2026；空=未标注）
    completed = Column(Integer, nullable=True, default=0, index=True)  # 0=进行中 1=已完结（完结后不再产生风险提醒）
    completed_at = Column(String, nullable=True)       # 完结时间
    completion_note = Column(Text, nullable=True)      # 完结说明/验收结论
    created_at = Column(DateTime, default=datetime.utcnow)

    files = relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")


class ProjectFile(Base):
    """项目文件表"""
    __tablename__ = "project_files"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    original_name = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    content = Column(Text, nullable=True)
    category = Column(String(20), nullable=True)   # 文件大类：合同 / 资料 / 成果 / 附件 / 其他
    doc_type = Column(String(50), nullable=True)   # 资料分类（16 类之一）或成果子类型（专利/论文/软著/获奖/标准）
    stage = Column(String(20), nullable=True)      # 项目阶段
    ai_summary = Column(Text, nullable=True)       # AI 一句话摘要
    processing_status = Column(String(20), nullable=True, default="done")  # done / processing / error
    amount = Column(Float, nullable=True)          # 财务单据金额（发票/结算单/暂估单/估算单等有金额单据）
    doc_date = Column(String, nullable=True)       # 财务单据发生日期
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="files")

class ContractSummary(Base):
    """合同摘要表：上传时提取一次，之后统计/检索直接查这张表"""
    __tablename__ = "contract_summaries"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, nullable=False, index=True)      # 关联 project_files.id
    project_id = Column(Integer, nullable=True, index=True)    # 冗余存项目，方便按项目统计

    contract_name = Column(String, nullable=True)    # 合同名称
    contract_no = Column(String, nullable=True)      # 编号
    party_a = Column(String, nullable=True)          # 甲方
    party_b = Column(String, nullable=True)          # 乙方
    amount_value = Column(Float, nullable=True)      # 金额数值（纯数字，可求和）
    currency = Column(String, nullable=True)         # 币种
    service_content = Column(Text, nullable=True)    # 服务内容
    sign_date = Column(String, nullable=True)        # 签署日期
    term = Column(String, nullable=True)             # 期限
    contract_type = Column(String, nullable=True)    # 类型
    service_period = Column(String, nullable=True)   # 服务周期
    created_at = Column(String, nullable=True)       # 提取时间

class DocumentSummary(Base):
    """项目资料摘要表：每份项目资料（方案/报告/纪要/申请表等）一条记录，用于精确统计与完整总结"""
    __tablename__ = "document_summaries"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, nullable=False, index=True)      # 关联 project_files.id
    project_id = Column(Integer, nullable=True, index=True)    # 冗余存项目，方便按项目统计

    doc_type = Column(String, nullable=True)   # 文档类型（如：科技项目申请书、可行性研究报告…）
    summary = Column(Text, nullable=True)      # 这份文件的一句话摘要
    created_at = Column(String, nullable=True) # 提取时间
    #summary = Column(Text, nullable=True)
    stage = Column(String(20), nullable=True)   # 项目阶段：项目立项/项目投决/项目实施/项目验收


class Contract(Base):
    """合同管理模块：独立的合同台账，存储合同文件本体 + 抽取的关键字段 + 履约到期状态"""
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, index=True)
    original_name = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    content = Column(Text, nullable=True)          # 提取的全文（用于预览/检索）

    contract_name = Column(String, nullable=True)  # 合同名称
    contract_no = Column(String, nullable=True)    # 合同编号
    party_a = Column(String, nullable=True)        # 甲方/发包方
    party_b = Column(String, nullable=True)        # 乙方/承包方
    amount_value = Column(Float, nullable=True)    # 金额数值（纯数字，可求和）
    currency = Column(String, nullable=True)       # 币种
    service_content = Column(Text, nullable=True)  # 工作内容/服务内容
    sign_date = Column(String, nullable=True)      # 签署日期
    start_date = Column(String, nullable=True)     # 履约开始时间
    end_date = Column(String, nullable=True)       # 履约结束时间/到期时间
    due_date = Column(String, nullable=True)       # 到期日（end_date 或 sign_date+term 推断）
    term = Column(String, nullable=True)           # 履约期限
    contract_type = Column(String, nullable=True)  # 合同类型
    processing_status = Column(String(20), nullable=True, default="done")  # done / processing / error
    # ---- 新增：项目关联 + 金额拆分 + 质保金 + 人工确认 + 执行计划 ----
    project_id = Column(Integer, nullable=True, index=True)   # 关联项目（1 项目 → 多合同）
    amount_ex_tax = Column(Float, nullable=True)              # 不含税金额
    tax_rate = Column(Float, nullable=True)                   # 税率（如 0.06）
    tax_amount = Column(Float, nullable=True)                 # 税额
    amount_incl_tax = Column(Float, nullable=True)            # 含税金额
    warranty_ratio = Column(Float, nullable=True)             # 质保金比例
    warranty_period = Column(String, nullable=True)           # 质保金到期时长（如 12 个月 / 1 年）
    warranty_due_date = Column(String, nullable=True)         # 质保金到期日
    confirmed = Column(Integer, nullable=True, default=0)     # 0=未人工确认 1=已确认生效
    confirmed_at = Column(String, nullable=True)              # 确认时间
    plan_generated = Column(Integer, nullable=True, default=0)  # 0=未生成执行计划 1=已生成
    source_file_id = Column(Integer, nullable=True, index=True)  # 来源项目文件 id（从文件管理导入时写入，用于幂等去重）
    alert_ignored = Column(Integer, nullable=True, default=0)  # 0=正常 1=已忽略风险预警（忽略后不再提示超期/临期）
    created_at = Column(DateTime, default=datetime.utcnow)


class ContractNode(Base):
    """合同执行计划节点：付款节点（预付款/进度款/验收款/质保金）+ 非付款业务节点（验收/进度报告等）"""
    __tablename__ = "contract_nodes"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    node_type = Column(String(20), nullable=False)     # payment 付款节点 / business 业务节点
    node_name = Column(String, nullable=False)          # 节点名称
    planned_date = Column(String, nullable=True)        # 计划日期（付款/交付日期）
    amount = Column(Float, nullable=True)               # 节点金额（付款节点）
    ratio = Column(Float, nullable=True)                # 节点比例（如质保金 5%）
    status = Column(String(20), nullable=True, default="待办")  # 待办 / 已完成 / 已取消
    remark = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContractNodeChange(Base):
    """执行计划节点变更记录：记录修改时间 + 修改原因，全程可追溯"""
    __tablename__ = "contract_node_changes"

    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, nullable=False, index=True)
    contract_id = Column(Integer, nullable=False, index=True)
    field = Column(String(50), nullable=True)           # 变更字段（日期/金额/状态/比例…）
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    change_reason = Column(String, nullable=True)       # 修改原因
    changed_at = Column(DateTime, default=datetime.utcnow)


class FinancialDoc(Base):
    """财务资料：暂估单 / 结算单 / 发票（1 合同 → 多张，可多次付款；发票只对应一个合同）"""
    __tablename__ = "financial_docs"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    doc_type = Column(String(20), nullable=False)       # 暂估单 / 结算单 / 发票
    original_name = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    amount = Column(Float, nullable=True)               # 金额
    tax_amount = Column(Float, nullable=True)           # 税额
    doc_date = Column(String, nullable=True)            # 发生日期
    confirmed = Column(Integer, nullable=True, default=0)  # 0=未人工确认 1=已确认
    remark = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Todo(Base):
    """待办：统一汇总节点待办、逾期付款、延期、质保金到期、超额、合同超期/临期等各类提醒"""
    __tablename__ = "todos"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(30), nullable=False)       # 节点待办/逾期付款/延期/质保金到期/超额/合同超期/合同临期…
    level = Column(String(10), nullable=False)          # danger / warning / info
    title = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    link = Column(String, nullable=True)                # 跳转模块名
    source_id = Column(Integer, nullable=True)          # 关联业务 id（节点/合同/经费…）
    status = Column(String(10), nullable=True, default="未处理")  # 未处理 / 已处理
    created_at = Column(DateTime, default=datetime.utcnow)
    handled_at = Column(DateTime, nullable=True)


class SystemConfig(Base):
    """系统可配置项：最终填充金额口径、超额判定基准、延期判定基准、超额容忍比例等"""
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), unique=True, nullable=False)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """审计日志表：记录登录、增删改、下载、问答等关键操作"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    username = Column(String, nullable=True)   # 操作人（登录用户；内部服务为 bot）
    action = Column(String, nullable=False)    # 如 "POST /projects"、"GET /files/1/download"
    detail = Column(Text, nullable=True)       # 补充信息（文件名、项目名、失败原因等）
    ip = Column(String, nullable=True)         # 来源 IP


class Funding(Base):
    """经费管理：统一经费明细台账（预算 / 到账 / 支出）

    设计说明（v2）：
    - 一行 = 一条经费明细，年度矩阵是这张表的透视视图，不再单独维护“单元格”作为唯一数据源；
    - category（经费科目）与 source（经费来源）用于分类查询；
    - contract_id 可空，关联合同后可追溯“这笔钱花在哪个合同上”；
    - legacy_cell_id 仅用于旧 funding_cells 数据的一次性迁移幂等。
    """
    __tablename__ = "fundings"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)   # 关联项目，可空=平台级经费
    contract_id = Column(Integer, nullable=True, index=True)  # 关联合同（共享合同场景下与 project_id 一起定位分摊项目）
    item_name = Column(String, nullable=False)   # 经费科目/名称/摘要
    fund_type = Column(String, nullable=True)    # 类型：预算 / 到账 / 支出
    amount = Column(Float, nullable=False)       # 金额（元）
    fund_date = Column(String, nullable=True)    # 日期（到账/支出日期）
    year = Column(Integer, nullable=True, index=True)   # 归属年度（由 fund_date 推导，可手工修正）
    month = Column(Integer, nullable=True)       # 归属月份 1~12（年度矩阵用）
    category = Column(String(50), nullable=True, index=True)  # 经费科目分类：设备费/材料费/劳务费…
    source = Column(String(50), nullable=True, index=True)    # 经费来源：国拨/省拨/集团拨款/企业自筹…
    voucher_no = Column(String(80), nullable=True)            # 凭证号/单据号
    status = Column(String(10), nullable=True, default="已确认")  # 已确认 / 待确认
    remark = Column(String, nullable=True)       # 备注
    legacy_cell_id = Column(Integer, nullable=True, unique=True, index=True)  # 旧年度一览表单元格 id（迁移幂等）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ContractProjectShare(Base):
    """合同-项目分摊：一个合同可被多个项目共用，每个项目只计自己的分摊额，避免合同金额被重复计算。

    - 未配置分摊行时，合同金额 100% 归属合同的主项目（Contract.project_id），兼容历史数据；
    - 配置分摊行后，各项目按 amount 计；未分完的余额自动归主项目（前端会提示“未分摊余额”）；
    - ratio 仅作辅助展示，实际金额以 amount 为准；amount 为空时按 ratio × 合同含税金额计算。
    """
    __tablename__ = "contract_project_shares"
    __table_args__ = (UniqueConstraint("contract_id", "project_id", name="uq_contract_share_cp"),)

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    amount = Column(Float, nullable=True)       # 该项目分摊金额（元）
    ratio = Column(Float, nullable=True)        # 分摊比例（0~1，辅助）
    remark = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class FundingGridRow(Base):
    """年度经费一览表·行：某年度纳入一览表的项目（可增删、排序，下次打开自动恢复）"""
    __tablename__ = "funding_grid_rows"

    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False, index=True)          # 年度
    project_id = Column(Integer, nullable=False, index=True)    # 关联项目
    sort_order = Column(Integer, nullable=False, default=0)     # 行顺序
    created_at = Column(DateTime, default=datetime.utcnow)


class FundingCell(Base):
    """年度经费一览表·单元格：项目 × 月份 的经费信息"""
    __tablename__ = "funding_cells"

    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False)         # 1~12
    amount = Column(Float, nullable=True)           # 投入金额
    item_name = Column(String, nullable=True)       # 经费科目/名称
    fund_date = Column(String, nullable=True)       # 经费发生日期
    created_at = Column(DateTime, default=datetime.utcnow)


class FundingAttachment(Base):
    """经费附件：优先精确关联到经费明细（funding_id）；旧数据仍按 项目+月份 关联。"""
    __tablename__ = "funding_attachments"

    id = Column(Integer, primary_key=True, index=True)
    funding_id = Column(Integer, nullable=True, index=True)  # 关联 fundings.id（新明细附件）
    year = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False)
    file_name = Column(String, nullable=False)      # 原始文件名
    file_path = Column(String, nullable=False)      # 磁盘存储路径
    created_at = Column(DateTime, default=datetime.utcnow)


class Achievement(Base):
    """成果台账：专利/论文/软著/获奖/标准等成果的独立台账"""
    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)   # 关联项目
    name = Column(String, nullable=True)          # 成果名称（自动识别时可能暂时为空，识别后回填/可编辑）
    category = Column(String, nullable=True)      # 类型：专利/论文/软件著作权/获奖/标准/成果登记/鉴定报告/其他
    status = Column(String, nullable=True)        # 状态：在研/已授权/已发表/已登记/已获奖/已发布/其他
    holder = Column(String, nullable=True)        # 权利人/作者/完成人
    application_date = Column(String, nullable=True)   # 申请日期（从专利/软著等证书提取的申请日）
    achieve_date = Column(String, nullable=True)  # 取得日期
    remark = Column(String, nullable=True)        # 备注
    file_name = Column(String, nullable=True)     # 上传的成果文件名（可下载查看）
    file_path = Column(String, nullable=True)     # 文件存储路径
    processing_status = Column(String(20), nullable=True, default="done")  # done/processing/error
    source = Column(String(20), nullable=True, default="manual")  # manual 手动 / auto 自动识别 / file 从文件管理同步
    source_file_id = Column(Integer, nullable=True)  # 来源文件（project_files.id），非空表示从文件管理同步而来
    created_at = Column(DateTime, default=datetime.utcnow)


class ProjectContractAmountOverride(Base):
    """年度经费一览表·合同金额人工覆盖：override 优先于自动聚合值（删除记录即恢复自动值）。"""
    __tablename__ = "project_contract_amount_override"
    __table_args__ = (UniqueConstraint("project_id", "year", "kind", name="uq_contract_amount_override_pyk"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    kind = Column(String(20), nullable=False)     # estimate 暂估额 / settlement 结算金额 / income 实际到账流水
    amount = Column(Float, nullable=True)         # 人工填写的金额（元）
    updated_at = Column(DateTime, default=datetime.utcnow)


class AlertDismissed(Base):
    """风险预警关闭记录：alert_key 唯一，INSERT OR IGNORE 幂等；关闭后预警/待办/角标三处一致不显示。"""
    __tablename__ = "alert_dismissed"

    id = Column(Integer, primary_key=True, index=True)
    alert_key = Column(String(120), unique=True, nullable=False, index=True)  # 格式 "{category}|{source_id}"
    category = Column(String(30), nullable=True)
    dismissed_at = Column(DateTime, default=datetime.utcnow)
    operator = Column(String, nullable=True, default="admin")


# ============ 网络通讯台账（net_*）：站点 / 定级系统 / 设备 / IP-MAC / 拓扑 / 等保资料 ============
class NetSite(Base):
    """站点/管理节点：集控中心、各电站、开关站"""
    __tablename__ = "net_sites"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(30), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    company = Column(String(50), nullable=True)      # 木里河 / 唐央 / 木里新能
    site_type = Column(String(20), nullable=True)    # 水电站/光伏电站/风电场/开关站/集控中心
    region = Column(String(100), nullable=True)
    address = Column(String(200), nullable=True)
    manager = Column(String(50), nullable=True)
    phone = Column(String(30), nullable=True)
    sort_order = Column(Integer, default=0)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NetSystem(Base):
    """定级系统（等保对象）"""
    __tablename__ = "net_systems"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("net_sites.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    level_s = Column(String(5), nullable=True)       # S2 / S3
    level_a = Column(String(5), nullable=True)       # A2 / A3
    level = Column(String(10), nullable=True)        # 二级 / 三级
    deploy_zone = Column(String(50), nullable=True)
    extension = Column(String(50), nullable=True)    # 工业控制系统
    filing_no = Column(String(60), nullable=True)
    filing_date = Column(String(20), nullable=True)
    filing_org = Column(String(100), nullable=True)
    integrator = Column(String(120), nullable=True)
    sys_version = Column(String(120), nullable=True)
    manager = Column(String(50), nullable=True)
    status = Column(String(20), default="在用")
    last_eval_date = Column(String(20), nullable=True)
    last_conclusion = Column(String(20), nullable=True)
    next_eval_date = Column(String(20), nullable=True)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NetDevice(Base):
    """设备台账：5 大类共用（网络/安全/服务器存储/终端/其他）"""
    __tablename__ = "net_devices"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("net_sites.id"), nullable=False, index=True)
    system_id = Column(Integer, ForeignKey("net_systems.id"), nullable=True, index=True)
    category = Column(String(20), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    brand_model = Column(String(200), nullable=True)
    is_virtual = Column(String(5), default="否")
    os_version = Column(String(120), nullable=True)
    purpose = Column(String(200), nullable=True)
    importance = Column(String(10), default="重要")
    zone = Column(String(30), nullable=True)
    room = Column(String(100), nullable=True)
    mgmt_ip = Column(String(120), nullable=True)
    mac = Column(String(30), nullable=True)
    is_hot_standby = Column(String(5), default="否")
    quantity = Column(Integer, default=1)
    biz_app = Column(String(200), nullable=True)
    db_version = Column(String(120), nullable=True)
    middleware = Column(String(120), nullable=True)
    antivirus = Column(String(120), nullable=True)
    asset_no = Column(String(60), nullable=True)
    sn = Column(String(100), nullable=True)
    owner = Column(String(50), nullable=True)
    rack = Column(String(50), nullable=True)
    warranty_end = Column(String(20), nullable=True)
    status = Column(String(20), default="在用")
    source = Column(String(20), default="manual")
    last_check_at = Column(String(20), nullable=True)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)


class NetAddress(Base):
    """IP/MAC 台账：办公终端与固定地址，不管理网段/VLAN/端口"""
    __tablename__ = "net_addresses"

    id = Column(Integer, primary_key=True, index=True)
    ip = Column(String(40), nullable=True, index=True)
    mac = Column(String(20), nullable=True, index=True)     # 标准化后
    mac_raw = Column(String(40), nullable=True)             # 原始文本
    department = Column(String(60), nullable=True)
    person = Column(String(40), nullable=True)
    device_type = Column(String(30), nullable=True)
    location = Column(String(100), nullable=True)
    site_id = Column(Integer, ForeignKey("net_sites.id"), nullable=True, index=True)
    status = Column(String(20), default="在用")
    wangjian = Column(String(20), nullable=True)            # 已安装/未安装/不适用
    remark = Column(Text, nullable=True)
    source = Column(String(20), default="import")
    first_seen = Column(String(20), nullable=True)
    updated_at = Column(DateTime, nullable=True)


class NetTopology(Base):
    """拓扑图：图片 + 缩略图 + 源文件，按版本管理"""
    __tablename__ = "net_topologies"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, nullable=True, index=True)
    system_id = Column(Integer, ForeignKey("net_systems.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    kind = Column(String(20), default="网络拓扑图")     # 网络拓扑图/接入点图/总体拓扑
    version = Column(String(20), nullable=True)
    effective_date = Column(String(20), nullable=True)
    image_path = Column(String(500), nullable=False)
    thumb_path = Column(String(500), nullable=True)
    source_path = Column(String(500), nullable=True)
    note = Column(Text, nullable=True)
    is_current = Column(Integer, default=1)
    uploaded_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NetReport(Base):
    """等保资料：测评报告/备案证明/整改报告/自查报告"""
    __tablename__ = "net_reports"

    id = Column(Integer, primary_key=True, index=True)
    system_id = Column(Integer, ForeignKey("net_systems.id"), nullable=False, index=True)
    site_id = Column(Integer, nullable=True, index=True)
    doc_type = Column(String(20), nullable=False, index=True)
    report_year = Column(Integer, nullable=True, index=True)
    project_no = Column(String(40), nullable=True)
    report_no = Column(String(60), nullable=True)
    level = Column(String(10), nullable=True)
    level_s = Column(String(5), nullable=True)
    level_a = Column(String(5), nullable=True)
    agency = Column(String(120), nullable=True)
    eval_start = Column(String(20), nullable=True)
    eval_end = Column(String(20), nullable=True)
    report_date = Column(String(20), nullable=True)
    conclusion = Column(String(20), nullable=True)
    pass_rate = Column(Float, nullable=True)
    item_total = Column(Integer, nullable=True)
    item_pass = Column(Integer, nullable=True)
    item_partial = Column(Integer, nullable=True)
    item_fail = Column(Integer, nullable=True)
    item_na = Column(Integer, nullable=True)
    problem_count = Column(Integer, nullable=True)
    major_risk = Column(Integer, default=0)
    last_eval_date = Column(String(20), nullable=True)
    last_score = Column(Float, nullable=True)
    last_conclusion = Column(String(20), nullable=True)
    next_due = Column(String(20), nullable=True)
    file_path = Column(String(500), nullable=False)
    original_name = Column(String(300), nullable=False)
    file_role = Column(String(10), default="主件")       # 主件/附件
    size = Column(Integer, nullable=True)
    text_path = Column(String(500), nullable=True)
    auto_type = Column(String(20), nullable=True)
    auto_system = Column(String(200), nullable=True)
    auto_year = Column(Integer, nullable=True)
    confidence = Column(Integer, default=0)
    confirmed = Column(Integer, default=0)
    remark = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class NetFinding(Base):
    """整改项：来自报告 3.14/5.1/8.1/附录B，或手工新增"""
    __tablename__ = "net_findings"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("net_reports.id"), nullable=True, index=True)
    system_id = Column(Integer, index=True, nullable=True)
    problem_no = Column(Integer, nullable=True)
    security_class = Column(String(30), nullable=True)
    control_point = Column(String(30), nullable=True)
    target = Column(String(300), nullable=True)
    requirement = Column(Text, nullable=True)
    description = Column(Text, nullable=False)
    risk_level = Column(String(10), nullable=True)
    suggestion = Column(Text, nullable=True)
    source = Column(String(20), default="report")        # report/prev_year/manual
    prev_status = Column(String(20), nullable=True)
    owner = Column(String(50), nullable=True)
    plan_date = Column(String(20), nullable=True)
    done_date = Column(String(20), nullable=True)
    status = Column(String(20), default="待整改")
    evidence_path = Column(String(500), nullable=True)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NetChange(Base):
    """字段级变更日志（网络台账）"""
    __tablename__ = "net_changes"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(20), nullable=False, index=True)
    entity_id = Column(Integer, nullable=True)
    entity_label = Column(String(200), nullable=True)
    field = Column(String(50), nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(String(200), nullable=True)
    operator = Column(String(50), nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)


class NetImportBatch(Base):
    """导入批次：保存差异快照，支持回滚"""
    __tablename__ = "net_import_batches"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(20), nullable=False)
    file_name = Column(String(300), nullable=True)
    total = Column(Integer, default=0)
    inserted = Column(Integer, default=0)
    updated = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    detail_json = Column(Text, nullable=True)
    operator = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NetDict(Base):
    """字典：部门/地点/品牌/用途 的别名归一"""
    __tablename__ = "net_dicts"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    alias = Column(String(200), nullable=True)
    sort_order = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_net_dict_kind_name"),)


class AIAudit(Base):
    """AI 外发审计：只记元数据，不记 prompt/回答正文。"""
    __tablename__ = "ai_audit"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    username = Column(String(50), nullable=True)
    module = Column(String(40), nullable=True)       # classify/contract_extract/ask...
    ref_type = Column(String(20), nullable=True)     # file/contract/report/query
    ref_id = Column(Integer, nullable=True)
    sensitivity = Column(String(5), nullable=True)   # L1~L4
    chars = Column(Integer, nullable=True)
    action = Column(String(20), nullable=True)       # sent/blocked/redacted
    reason = Column(String(200), nullable=True)
    endpoint = Column(String(100), nullable=True)
    model = Column(String(50), nullable=True)
    request_id = Column(String(60), nullable=True)


# 建表
Base.metadata.create_all(bind=engine)
# 兼容旧数据库：如果 project_files 表已存在但没有 content 列，自动补上
_inspector = inspect(engine)
if "document_summaries" in _inspector.get_table_names():
    _cols = [c["name"] for c in _inspector.get_columns("document_summaries")]
    if "stage" not in _cols:
        with engine.begin() as _conn:
            _conn.execute(text("ALTER TABLE document_summaries ADD COLUMN stage VARCHAR(20)"))
        print("[迁移] 已为 document_summaries 表添加 stage 列")

# 兼容旧数据库：project_files 增加分类相关列（category / doc_type / stage / ai_summary）
if "project_files" in _inspector.get_table_names():
    _fcols = [c["name"] for c in _inspector.get_columns("project_files")]
    for _col, _type in [
        ("category", "VARCHAR(20)"),
        ("doc_type", "VARCHAR(50)"),
        ("stage", "VARCHAR(20)"),
        ("ai_summary", "TEXT"),
        ("processing_status", "VARCHAR(20)"),
        ("amount", "FLOAT"),
        ("doc_date", "VARCHAR"),
    ]:
        if _col not in _fcols:
            with engine.begin() as _conn:
                _conn.execute(text(f"ALTER TABLE project_files ADD COLUMN {_col} {_type}"))
            print(f"[迁移] 已为 project_files 表添加 {_col} 列")

# 兼容旧数据库：contracts 增加处理状态 + 项目关联 + 金额拆分 + 质保金 + 确认/计划列
if "contracts" in _inspector.get_table_names():
    _ccols = [c["name"] for c in _inspector.get_columns("contracts")]
    for _col, _type in [
        ("processing_status", "VARCHAR(20)"),
        ("project_id", "INTEGER"),
        ("amount_ex_tax", "FLOAT"),
        ("tax_rate", "FLOAT"),
        ("tax_amount", "FLOAT"),
        ("amount_incl_tax", "FLOAT"),
        ("warranty_ratio", "FLOAT"),
        ("warranty_period", "VARCHAR"),
        ("warranty_due_date", "VARCHAR"),
        ("confirmed", "INTEGER"),
        ("confirmed_at", "VARCHAR"),
        ("plan_generated", "INTEGER"),
        ("source_file_id", "INTEGER"),
        ("alert_ignored", "INTEGER"),
    ]:
        if _col not in _ccols:
            with engine.begin() as _conn:
                _conn.execute(text(f"ALTER TABLE contracts ADD COLUMN {_col} {_type}"))
            print(f"[迁移] 已为 contracts 表添加 {_col} 列")

# 兼容旧数据库：achievements 增加文件关联/识别状态/来源列
if "achievements" in _inspector.get_table_names():
    _acols = [c["name"] for c in _inspector.get_columns("achievements")]
    # 旧库 accept_date 列改名为 application_date（受理日期 → 申请日期，语义更正）
    if "accept_date" in _acols and "application_date" not in _acols:
        with engine.begin() as _conn:
            _conn.execute(text("ALTER TABLE achievements RENAME COLUMN accept_date TO application_date"))
        # 手动更新列名列表（避免 SQLAlchemy inspector 缓存导致重复 ADD COLUMN）
        _acols = ["application_date" if c == "accept_date" else c for c in _acols]
        print("[迁移] achievements.accept_date 已重命名为 application_date")
    for _col, _type in [
        ("application_date", "VARCHAR"),
        ("file_name", "VARCHAR(500)"),
        ("file_path", "VARCHAR(1000)"),
        ("processing_status", "VARCHAR(20)"),
        ("source", "VARCHAR(20)"),
        ("source_file_id", "INTEGER"),
    ]:
        if _col not in _acols:
            with engine.begin() as _conn:
                _conn.execute(text(f"ALTER TABLE achievements ADD COLUMN {_col} {_type}"))
            print(f"[迁移] 已为 achievements 表添加 {_col} 列")

    # achievements.name 从 NOT NULL 放宽为可空（上传成果文件自动识别时 name 暂空，后台识别后回填）
    # SQLite 不支持 ALTER COLUMN 去 NOT NULL，需重建表
    _a_full = {c["name"]: c for c in _inspector.get_columns("achievements")}
    _name_col = _a_full.get("name")
    if _name_col is not None and _name_col.get("nullable") is False:
        with engine.begin() as _conn:
            _conn.execute(text("ALTER TABLE achievements RENAME TO achievements_old"))
            _conn.execute(text("""
                CREATE TABLE achievements (
                    id INTEGER NOT NULL PRIMARY KEY,
                    project_id INTEGER,
                    name VARCHAR,
                    category VARCHAR,
                    status VARCHAR,
                    holder VARCHAR,
                    application_date VARCHAR,
                    achieve_date VARCHAR,
                    remark VARCHAR,
                    file_name VARCHAR(500),
                    file_path VARCHAR(1000),
                    processing_status VARCHAR(20),
                    source VARCHAR(20),
                    source_file_id INTEGER,
                    created_at DATETIME
                )
            """))
            _conn.execute(text("""
                INSERT INTO achievements (id, project_id, name, category, status, holder, application_date, achieve_date, remark, file_name, file_path, processing_status, source, source_file_id, created_at)
                SELECT id, project_id, name, category, status, holder, application_date, achieve_date, remark, file_name, file_path, processing_status, source, source_file_id, created_at
                FROM achievements_old
            """))
            _conn.execute(text("DROP TABLE achievements_old"))
            _conn.execute(text("CREATE INDEX ix_achievements_id ON achievements (id)"))
            _conn.execute(text("CREATE INDEX ix_achievements_project_id ON achievements (project_id)"))
        print("[迁移] 已重建 achievements 表：name 列放宽为可空")

# 兼容旧数据库：projects 增加 stage 列（旧数据空值由聚合接口兜底为「项目立项」）
if "projects" in _inspector.get_table_names():
    _pcols = [c["name"] for c in _inspector.get_columns("projects")]
    if "stage" not in _pcols:
        with engine.begin() as _conn:
            _conn.execute(text("ALTER TABLE projects ADD COLUMN stage VARCHAR(20)"))
        print("[迁移] 已为 projects 表添加 stage 列")
    if "start_year" not in _pcols:
        with engine.begin() as _conn:
            _conn.execute(text("ALTER TABLE projects ADD COLUMN start_year INTEGER"))
        print("[迁移] 已为 projects 表添加 start_year 列")

# 兼容旧数据库：projects 增加「项目完结」列（完结项目不再产生风险提醒）
if "projects" in _inspector.get_table_names():
    _pcols = [c["name"] for c in _inspector.get_columns("projects")]
    for _col, _type in [
        ("completed", "INTEGER DEFAULT 0"),
        ("completed_at", "VARCHAR"),
        ("completion_note", "TEXT"),
    ]:
        if _col not in _pcols:
            with engine.begin() as _conn:
                _conn.execute(text(f"ALTER TABLE projects ADD COLUMN {_col} {_type}"))
            print(f"[迁移] 已为 projects 表添加 {_col} 列")
    with engine.begin() as _conn:
        _conn.execute(text("UPDATE projects SET completed = 0 WHERE completed IS NULL"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_completed ON projects (completed)"))

# 兼容旧数据库：fundings 升级为「统一经费明细台账」（年度/月份/科目/来源/合同/状态/凭证号）
if "fundings" in _inspector.get_table_names():
    _fcols = [c["name"] for c in _inspector.get_columns("fundings")]
    for _col, _type in [
        ("year", "INTEGER"),
        ("month", "INTEGER"),
        ("category", "VARCHAR(50)"),
        ("source", "VARCHAR(50)"),
        ("contract_id", "INTEGER"),
        ("voucher_no", "VARCHAR(80)"),
        ("status", "VARCHAR(10) DEFAULT '已确认'"),
        ("legacy_cell_id", "INTEGER"),
        ("updated_at", "DATETIME"),
    ]:
        if _col not in _fcols:
            with engine.begin() as _conn:
                _conn.execute(text(f"ALTER TABLE fundings ADD COLUMN {_col} {_type}"))
            print(f"[迁移] 已为 fundings 表添加 {_col} 列")
    with engine.begin() as _conn:
        # 历史 fund_date 形如 2026-09-24 / 2026.9.24 / 2026/9/24，均按前 4 位年份、第 6 位起月份兜底
        _conn.execute(text(
            "UPDATE fundings SET year = CAST(substr(fund_date, 1, 4) AS INTEGER) "
            "WHERE year IS NULL AND fund_date IS NOT NULL AND length(trim(fund_date)) >= 4"
        ))
        _conn.execute(text(
            "UPDATE fundings SET month = CAST(substr(fund_date, 6, 2) AS INTEGER) "
            "WHERE month IS NULL AND fund_date LIKE '____-__-__'"
        ))
        _conn.execute(text("UPDATE fundings SET status = '已确认' WHERE status IS NULL OR status = ''"))
        _conn.execute(text("UPDATE fundings SET updated_at = created_at WHERE updated_at IS NULL"))
        for _idx, _col in [
            ("ix_fundings_year", "year"),
            ("ix_fundings_category", "category"),
            ("ix_fundings_source", "source"),
            ("ix_fundings_contract_id", "contract_id"),
        ]:
            _conn.execute(text(f"CREATE INDEX IF NOT EXISTS {_idx} ON fundings ({_col})"))
        _conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_fundings_legacy_cell ON fundings (legacy_cell_id)"))

# 兼容旧数据库：funding_attachments 增加 funding_id（附件可精确挂到某条经费明细）
if "funding_attachments" in _inspector.get_table_names():
    _acols = [c["name"] for c in _inspector.get_columns("funding_attachments")]
    if "funding_id" not in _acols:
        with engine.begin() as _conn:
            _conn.execute(text("ALTER TABLE funding_attachments ADD COLUMN funding_id INTEGER"))
        print("[迁移] 已为 funding_attachments 表添加 funding_id 列")
    with engine.begin() as _conn:
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_funding_attachments_funding_id ON funding_attachments (funding_id)"))

# 一次性把旧「年度经费一览表」单元格迁移为经费明细（用 system_configs 标记，避免删除后重启又复活）
if "funding_cells" in _inspector.get_table_names() and "fundings" in _inspector.get_table_names():
    with engine.begin() as _conn:
        _done = _conn.execute(text(
            "SELECT value FROM system_configs WHERE key = 'funding_cells_migrated_v2'"
        )).fetchone()
        if _done is None:
            _rows = _conn.execute(text(
                "SELECT c.id, c.year, c.project_id, c.month, c.amount, c.item_name, c.fund_date, c.created_at "
                "FROM funding_cells c "
                "WHERE COALESCE(c.amount, 0) > 0 "
                "  AND c.id NOT IN (SELECT legacy_cell_id FROM fundings WHERE legacy_cell_id IS NOT NULL)"
            )).fetchall()
            for _r in _rows:
                _conn.execute(text(
                    "INSERT INTO fundings (project_id, contract_id, item_name, fund_type, amount, fund_date, "
                    "year, month, category, source, status, legacy_cell_id, remark, created_at, updated_at) "
                    "VALUES (:pid, NULL, :item, '支出', :amt, :fdate, :year, :month, '其他支出', '未分类', "
                    "'已确认', :legacy, '由旧年度一览表迁移', :created, :created)"
                ), {
                    "pid": _r[2], "item": (_r[5] or "历史年度经费"), "amt": _r[4], "fdate": _r[6],
                    "year": _r[1], "month": _r[3], "legacy": _r[0], "created": _r[7],
                })
            _conn.execute(text(
                "INSERT OR REPLACE INTO system_configs (key, value, updated_at) "
                "VALUES ('funding_cells_migrated_v2', :v, datetime('now'))"
            ), {"v": str(len(_rows))})
            if _rows:
                print(f"[迁移] 已将 {len(_rows)} 条旧年度经费单元格迁移为经费明细")

# 初始化系统默认配置（待定口径可配置项，INSERT OR IGNORE 幂等）
_DEFAULT_CONFIGS = {
    "final_amount_basis": "invoice",          # 最终填充金额口径：invoice=发票额 / estimate=暂估额 / both=三列并存
    "overrun_basis": "contract_incl_tax",     # 超额判定基准：contract_incl_tax=合同含税金额 / project_budget=项目预算总额
    "delay_basis": "contract_date",           # 延期判定基准：contract_date=合同约定付款/交付日期
    "overrun_tolerance": "0.0",               # 超额容忍比例（0.05 表示超出 5% 才报警）
}
with engine.begin() as _conn:
    for _k, _v in _DEFAULT_CONFIGS.items():
        _conn.execute(
            text("INSERT OR IGNORE INTO system_configs (key, value, updated_at) VALUES (:k, :v, datetime('now'))"),
            {"k": _k, "v": _v},
        )

