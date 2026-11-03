# 网络通讯台账模块 · 设计方案（v2 评审稿）

> 日期：2026-10-28
> 资料依据：`唐央调研表1.xlsx`、`木里水电调研表-2024.xlsx`、`木里新能源调研表.xlsx`、`木里河公司内网设备IP与MAC地址统计表（原表/整理后202609）`、`木里水电拓扑/*.jpg`、`木里资料/*.jpg|vsd|vsdx`、**2026 年 10 份等保 2.0 测评报告（docx，含盖章版）**
> 约束：单管理员维护；不引入 AI 抽取；拓扑图为 JPG；地址台账不管理网段/VLAN/端口/历史使用人，但必须 IP-MAC 绑定。

---

## 0. 结论摘要

1. 现有资料不是"零散台账"，而是**等保测评调研表 + 测评报告体系**：调研表 24 个工作表、设备分 5 类；测评报告结构固定（2.1.1 定级结果、3.14 问题汇总、5.1 风险分析、7 结论、8.1 整改建议、附录 B 前次整改）。台账按这套结构建，才能"录一次、常年复用、测评季直接出表/出清单"。
2. 已确认 **11 个站点节点**、**10 个定级系统**（2026 年 10 份报告实锤，含新增让白光伏；杜依暂无独立定级系统）。设备记录约 **343 条**（木里河 226 / 唐央 43 / 新能 74），三级 5 个、二级 5 个。
3. IP/MAC 台账现有 **197 条有效记录**，已发现 8 组重复 IP、5 组重复 MAC、9 条 MAC 格式异常、部门 25 种写法、地点 50+ 种写法——**清洗与冲突检测是刚需，不是加分项**。
4. 不用 AI 也能"智能"：10 份报告实测，文件名 + 正文规则可**100% 判出**系统、等级、测评周期、符合率、结论、问题数；每系统 25~49 项问题、合计 **359 项**可自动生成整改清单；拓扑图按文件名自动挂接；IP/MAC 自动标准化与查重；调研表一键反向生成。
5. 技术路线完全复用现有底座（FastAPI + SQLite + Streamlit + 现有待办/审计/备份），**不新增任何第三方依赖**（PyMuPDF、Pillow、openpyxl、python-docx 均已具备）。

---

## 1. 需求口径（已确认）

| 项 | 确认结果 | 设计影响 |
|---|---|---|
| 站点 | 木里河：集控中心、上通坝、卡基娃、俄公堡、立洲；唐央：大丰、绒达；新能：当依、汇康、杜依、**让白** | 站点主数据 11 条，作为一切数据的轴 |
| 设备边界 | 参照调研表 5 大类，要方便维护 | 一张设备表 + 5 类字段组，动态表单；不逐台管端口 |
| 地址台账 | 不管网段/VLAN/端口/历史使用人；要 MAC 绑定 | 地址表只保留"IP+MAC+人+部门+地点+状态" |
| 定级系统 | **10 个**（2026 报告实锤）；要存测评、备案、整改、自查 4 类资料 | 报告表按"系统 × 资料类型 × 年度"组织 |
| 维护人 | 仅 1 人 | 不做角色权限；记录操作人 + 变更日志 |
| 模板 | 已提供 3 套调研表 + IP/MAC 表 + 10 份测评报告 | 导入/导出按原表头，零学习成本 |
| 拓扑图 | JPG 图片 | 存图 + 缩略图 + 版本；支持 VSDX 源文件附带 |
| AI | 不能抽取 | 全部本地规则：关键词、正则、系统名匹配、表头映射、章节定位 |

---

## 2. 资料盘点与数据画像

### 2.1 站点（11 个站点节点）

| 公司 | 站点 | 类型 | 资料中出现 |
|---|---|---|---|
| 木里河公司 | 集控中心（成都本部） | 集控中心 | 调研表、2026 报告 |
| 木里河公司 | 上通坝电站 | 水电站 | 调研表、报告、IP 表 |
| 木里河公司 | 卡基娃电站 | 水电站 | 调研表、报告、IP 表 |
| 木里河公司 | 俄公堡电站 | 水电站 | 调研表、报告、IP 表 |
| 木里河公司 | 立洲电站 | 水电站 | 调研表、报告、IP 表 |
| 唐央公司 | 大丰风电场 | 风电场 | 2026 报告、IP 表 |
| 唐央公司 | 绒达光伏电站 | 光伏电站 | 调研表、报告、拓扑图、IP 表 |
| 木里新能 | 当依光伏电站 | 光伏电站 | 调研表、报告、拓扑图、IP 表 |
| 木里新能 | 汇康开关站 | 开关站 | 调研表、报告、拓扑图 |
| 木里新能 | 杜依光伏电站 | 光伏电站 | 拓扑图、IP 表（**无独立定级系统**） |
| 木里新能 | 让白光伏电站 | 光伏电站 | 2026 报告（**2026 新增**） |

> 注：你口述"九个站点"，2026 年资料实际为 **11 个站点节点 / 10 个定级系统**（新增让白；杜依有站点有地址但未见定级系统与调研表）。若杜依并入当依系统，系统数仍为 10、站点数 11。

### 2.2 定级系统（10 个，2026 报告实锤）

| # | 系统名称 | 等级 | 备案号 | 部署大区 |
|---|---|---|---|---|
| 1 | 集控中心计算机监控系统 | S2A3（三级） | 51000026011-19001 | 安全区Ⅰ/Ⅱ |
| 2 | 立洲电站计算机监控系统 | S2A2（二级） | 51342226672-19002 | 安全区Ⅰ |
| 3 | 俄公堡电站计算机监控系统 | S2A2（二级） | 51342226672-22003 | 安全区Ⅰ |
| 4 | 卡基娃电站计算机监控系统 | S2A2（二级） | 51342226672-22004 | 安全区Ⅰ |
| 5 | 上通坝电站计算机监控系统 | S2A2（二级） | 51342226672-19001 | 安全区Ⅰ |
| 6 | 大丰风电场电力监控系统 | S2A3（三级） | 待补 | 生产控制大区 |
| 7 | 绒达光伏电站电力监控系统 | S3A3（三级） | 51342226004-24001 | 生产控制 + 管理信息大区 |
| 8 | 220kV 当依光伏电站电力监控系统 | S2A3（三级） | 51342226003-23001 | 生产控制 + 管理信息大区 |
| 9 | 220kV 汇康开关站电力监控系统 | S2A3（三级） | 51342226003-24002 | 生产控制大区 |
| 10 | 让白光伏电站电力监控系统 | S2A2（二级） | 待补 | 待补 |

> 关键点：等保级别要拆成 **S（业务信息）+ A（服务保证）** 两列存储，`level = 三级 if S3 或 A3 else 二级`。测评周期默认"三级每年、二级每两年"，可配置。大丰、让白是 2026 新增报告，备案号需补录。

### 2.3 设备画像

| 单位 | 网络设备 | 安全设备 | 服务器/存储 | 终端设备 | 其他设备 | 合计 |
|---|---|---|---|---|---|---|
| 木里河（5 系统） | 62 | 83 | 41 | 17 | 23 | **226** |
| 唐央（仅绒达调研表） | 13 | 17 | 5 | 2 | 6 | **43** |
| 新能（当依+汇康，无让白） | 27 | 23 | 8 | 5 | 11 | **74** |
| 合计 | 102 | 123 | 54 | 24 | 40 | **343** |

> 注：343 条来自 3 套调研表；**大丰、让白两套 2026 新系统尚无设备调研数据**，需从报告附录 A 或现场补录（报告附录 A 有设备清单，但缺 IP/区域/机房，可作为导入来源之一）。

**调研表设备列（5 类并集）**：
`序号 / 所属系统 / 设备名称 / 是否虚拟设备 / 系统及版本 / 品牌及型号 / 用途 / 重要程度 / 备注 / 所属网络区域 / 物理环境名称 / IP地址(掩码) / 是否热备`，服务器与终端另加 `所属业务应用系统 / 操作系统 / 数据库 / 中间件 / 杀毒软件`，其他设备用 `设备类别/用途`。

**两个必须解决的数据问题**：
1. "备注"里混着台数（"1台""2台（非网管）"），一行可能代表多台 → 台账需支持 `数量` 字段，同时允许逐台登记（SN/资产编号唯一时）。
2. 调研表大量 IP 为 `－` 或 `192.198.*.*`（脱敏/未填）→ 设备表 `管理IP` 允许为空，不强制。

### 2.4 IP/MAC 台账画像（整理后 202609）

| 指标 | 数值 |
|---|---|
| 总行数 / 有效记录 | 209 / 197 |
| 有 IP / 唯一 IP | 195 / 187（**8 组重复**，其中 2 条为「未接网线」文本） |
| 有 MAC / 唯一 MAC | 188 / 179（**5 组重复**） |
| MAC 格式 | 短横 155、冒号 24、全角冒号 2、位数不足 1（`E0-BE-03-83-B0`）、非 MAC 文本 5（`固定IP`） |
| 异常 IP | `未接网线` 2 条；`192.168.8.x`、`11.86.12.x`、`51.102.149.x` 等非规划段 |
| 部门写法 | 25 种（`新能源运管中心` vs `新能源 运管中心`；`卡基娃` vs `卡基娃电站`；`计划发展部` vs `计划部`；`工程部` vs `工程管理部`…） |
| 地点写法 | 50+ 种（`成都办公楼` vs `成都三楼办公楼`；`中控室` vs `集控中心中控室`…） |
| 状态/附加 | `是否安装网鉴V01`（已安装 135 / 否 12）、备注（`停止使用` 等） |

→ 必须内建：**MAC 标准化、IP 校验、重复检测、部门/地点归一字典、异常清单**。

### 2.5 拓扑图

- 13 张 JPG（单张最大 18MB）+ 2 个 VSD/VSDX 源文件；
- 命名规律：`上通坝电站计算机监控系统.jpg`、`卡基娃电站计算机监控系统接入点.jpg`、`CP-B-010 绒达光伏电站电力监控系统网络拓扑图.jpg`、`220kV当依光伏电站电力监控系统.jpg`；
- 类型两类：**网络拓扑图**、**接入点图**；
- 设计：原图 + 缩略图（列表秒开）+ 源文件（可下载编辑）+ 版本/生效日期。

### 2.6 等保资料

| 类型 | 内容 | 存储要求 | 2026 年现状 |
|---|---|---|---|
| 测评报告 | 年度测评报告（定级结果/问题/风险/结论/整改建议） | 按系统 + 年度 | 10 份 docx + 10 份 PDF |
| 备案证明 | 备案证明/备案表 | 按系统，通常 1 份 | 未随本次资料提供 |
| 整改报告 | 整改情况报告 | 按系统，可多次 | 待生成 |
| 自查报告 | 年度自查 | 按系统 + 年度 | 未随本次资料提供 |

### 2.7 2026 年测评结果实况（规则解析 10/10 成功）

| 系统 | 单位 | 等级 | 测评项 | 符合/部分/不符合/不适用 | 符合率 | 问题数 | 结论 |
|---|---|---|---|---|---|---|---|
| 上通坝电站计算机监控系统 | 木里河 | 二级 | 171 | 120/21/7/23 | 81.08% | 29 | 基本符合 |
| 俄公堡电站计算机监控系统 | 木里河 | 二级 | 171 | 121/20/7/23 | 81.76% | 28 | 基本符合 |
| 卡基娃电站计算机监控系统 | 木里河 | 二级 | 171 | 124/17/7/23 | 83.78% | 25 | 基本符合 |
| 立洲电站计算机监控系统 | 木里河 | 二级 | 171 | 120/21/7/23 | 81.08% | 29 | 基本符合 |
| 集控中心计算机监控系统 | 木里河 | 三级 | 250 | 174/25/10/41 | 83.25% | 37 | 基本符合 |
| 大丰风电场电力监控系统 | 唐央 | 三级 | 250 | 178/25/15/32 | 81.65% | 41 | 基本符合 |
| 绒达光伏电站电力监控系统 | 唐央 | 三级 | 259 | 180/33/14/32 | 79.30% | 49 | 基本符合 |
| 220kV 当依光伏电站电力监控系统 | 木里新能 | 三级 | 250 | 174/28/16/32 | 79.82% | 46 | 基本符合 |
| 220kV 汇康开关站电力监控系统 | 木里新能 | 三级 | 250 | 169/22/20/39 | 80.09% | 42 | 基本符合 |
| 让白光伏电站电力监控系统 | 木里新能 | 二级 | 171 | 122/22/9/18 | 79.74% | 33 | 基本符合 |
| **合计** | | | | | | **359** | 全部无重大风险 |

**三个结论**：
1. 报告元数据（编号、单位、系统、等级、测评周期、符合率、结论、测评项分布、上次得分）**完全可由文件名 + 章节定位 + 正则读出**，10 份全部命中，无需 AI。
2. 每系统 25~49 项问题、合计 **359 项**整改建议，手工录入不现实 → **必须支持从 3.14 / 5.1 / 8.1 / 附录 B 自动生成整改清单**。
3. 2024 年为综合得分（约 81.2，结论"良"），2026 年改为符合率（79.3~83.8%）+ 结论"基本符合"，**两者口径不同，不能直接比较**；台账按年度分别存原值，趋势只做参考。绒达（79.30%）、让白（79.74%）、当依（79.82%）符合率最低，应作为整改重点。

---

## 3. 模块定位与信息架构

> **一句话**：以站点为轴、以定级系统为单元、以设备和地址为对象、以变更和合规为提醒的网络通讯资产台账。

```
公司
├─ 站点（11）
│   ├─ 机房/物理环境（文本，不单独建表）
│   ├─ 定级系统（10）── 备案信息 / 测评结论 / 下次测评日
│   │   ├─ 设备（5 类，343 条）
│   │   ├─ 拓扑图（版本 + 生效日期 + 类型）
│   │   └─ 等保资料（测评/备案/整改/自查）── 整改项（2026 共 359 项）
│   └─ 内网地址（197 条：IP + MAC + 人 + 部门 + 地点 + 状态）
└─ 全局：变更日志 / 导入批次 / 数据质量 / 待办
```

**刻意不做的**（避免过度设计）：端口表、链路表、网段/VLAN 表、机柜 U 位、自动发现/扫描、角色权限、地址历史使用人。

---

## 4. 数据模型（9 张业务表 + 1 张字典表）

### 4.1 表清单

| 表 | 作用 | 关键约束 |
|---|---|---|
| `net_sites` | 站点/管理节点 | `code` 唯一 |
| `net_systems` | 定级系统 | 关联站点；`filing_no` 可空唯一 |
| `net_devices` | 设备（5 类共用） | 关联站点+系统；`category` 枚举 |
| `net_addresses` | IP/MAC 台账 | `ip`、`mac` 建索引但不唯一（冲突靠检测） |
| `net_topologies` | 拓扑图 | 关联系统；`is_current` 标当前版本 |
| `net_reports` | 等保资料 | 关联系统；`doc_type + report_year` |
| `net_findings` | 整改项 | 关联报告/系统 |
| `net_changes` | 字段级变更日志 | 实体+字段+旧值/新值 |
| `net_import_batches` | 导入批次（可回滚） | 保存差异 JSON |
| `net_dicts` | 部门/地点/品牌/用途字典 | `kind + name` 唯一；`alias` 存别名 |

### 4.2 DDL（SQLite）

```sql
CREATE TABLE net_sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code VARCHAR(30) UNIQUE NOT NULL,
  name VARCHAR(100) NOT NULL,
  company VARCHAR(50),                -- 木里河/唐央/木里新能
  site_type VARCHAR(20),              -- 水电站/光伏电站/开关站/集控中心/办公区
  region VARCHAR(100),
  address VARCHAR(200),
  manager VARCHAR(50),
  phone VARCHAR(30),
  sort_order INTEGER DEFAULT 0,
  remark TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE net_systems (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES net_sites(id),
  name VARCHAR(200) NOT NULL,
  level_s VARCHAR(5),                 -- S2 / S3
  level_a VARCHAR(5),                 -- A2 / A3
  level VARCHAR(10),                  -- 二级 / 三级（由 S/A 推导）
  deploy_zone VARCHAR(50),
  extension VARCHAR(50),              -- 工业控制系统 等
  filing_no VARCHAR(60),
  filing_date VARCHAR(20),
  filing_org VARCHAR(100),
  integrator VARCHAR(100),
  sys_version VARCHAR(100),
  manager VARCHAR(50),
  status VARCHAR(20) DEFAULT '在用',
  last_eval_date VARCHAR(20),
  last_conclusion VARCHAR(20),
  next_eval_date VARCHAR(20),
  remark TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_net_systems_site ON net_systems(site_id);

CREATE TABLE net_devices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES net_sites(id),
  system_id INTEGER REFERENCES net_systems(id),
  category VARCHAR(20) NOT NULL,      -- 网络设备/安全设备/服务器存储/终端设备/其他设备
  name VARCHAR(200) NOT NULL,
  brand_model VARCHAR(200),
  is_virtual VARCHAR(5) DEFAULT '否',
  os_version VARCHAR(100),
  purpose VARCHAR(200),
  importance VARCHAR(10) DEFAULT '重要',   -- 关键/重要/一般
  zone VARCHAR(30),                   -- 安全区Ⅰ/Ⅱ/Ⅲ
  room VARCHAR(100),                  -- 物理环境名称
  mgmt_ip VARCHAR(100),
  mac VARCHAR(30),
  is_hot_standby VARCHAR(5) DEFAULT '否',
  quantity INTEGER DEFAULT 1,         -- 同型号合并登记时的台数
  biz_app VARCHAR(200),
  db_version VARCHAR(100),
  middleware VARCHAR(100),
  antivirus VARCHAR(100),
  asset_no VARCHAR(60),
  sn VARCHAR(100),
  owner VARCHAR(50),
  rack VARCHAR(50),
  warranty_end VARCHAR(20),
  status VARCHAR(20) DEFAULT '在用',  -- 在用/备用/维修/报废
  source VARCHAR(20) DEFAULT 'manual',
  last_check_at VARCHAR(20),
  remark TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME
);
CREATE INDEX ix_net_devices_site ON net_devices(site_id);
CREATE INDEX ix_net_devices_system ON net_devices(system_id);
CREATE INDEX ix_net_devices_cat ON net_devices(category);

CREATE TABLE net_addresses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip VARCHAR(40),
  mac VARCHAR(20),                    -- 标准化：大写、冒号分隔
  mac_raw VARCHAR(40),                -- 原始文本（保留可追溯）
  department VARCHAR(60),
  person VARCHAR(40),
  device_type VARCHAR(30),            -- 台式电脑/笔记本/打印机/其他
  location VARCHAR(100),
  site_id INTEGER REFERENCES net_sites(id),
  status VARCHAR(20) DEFAULT '在用',  -- 在用/停用/待核实/未接网线
  wangjian VARCHAR(20),               -- 已安装/未安装/不适用
  remark TEXT,
  source VARCHAR(20) DEFAULT 'import',
  first_seen VARCHAR(20),
  updated_at DATETIME
);
CREATE INDEX ix_net_addr_ip ON net_addresses(ip);
CREATE INDEX ix_net_addr_mac ON net_addresses(mac);
CREATE INDEX ix_net_addr_site ON net_addresses(site_id);

CREATE TABLE net_topologies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER REFERENCES net_sites(id),
  system_id INTEGER REFERENCES net_systems(id),
  name VARCHAR(200) NOT NULL,
  kind VARCHAR(20) DEFAULT '网络拓扑图',   -- 网络拓扑图/接入点图/总体拓扑
  version VARCHAR(20),
  effective_date VARCHAR(20),
  image_path VARCHAR(500) NOT NULL,
  thumb_path VARCHAR(500),
  source_path VARCHAR(500),               -- vsd/vsdx 源文件
  note TEXT,
  is_current INTEGER DEFAULT 1,
  uploaded_by VARCHAR(50),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE net_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  system_id INTEGER NOT NULL REFERENCES net_systems(id),
  site_id INTEGER REFERENCES net_sites(id),
  doc_type VARCHAR(20) NOT NULL,      -- 测评报告/备案证明/整改报告/自查报告/其他
  report_year INTEGER,
  project_no VARCHAR(40),             -- 项目编号：NZSA202651-057-DB-ST
  report_no VARCHAR(60),              -- 报告编号（可同项目号）
  level VARCHAR(10),
  level_s VARCHAR(5),
  level_a VARCHAR(5),
  agency VARCHAR(120),                -- 南京南自数安技术有限公司
  eval_start VARCHAR(20),             -- 测评开始
  eval_end VARCHAR(20),               -- 测评结束
  report_date VARCHAR(20),            -- 出具日期
  conclusion VARCHAR(20),             -- 符合/基本符合/不符合
  pass_rate FLOAT,                    -- 符合率 %
  item_total INTEGER, item_pass INTEGER, item_partial INTEGER,
  item_fail INTEGER, item_na INTEGER, -- 测评项分布
  problem_count INTEGER,              -- 问题数（3.14 去重计数）
  major_risk INTEGER DEFAULT 0,       -- 是否重大风险隐患
  last_eval_date VARCHAR(20),         -- 上次测评
  last_score FLOAT,
  last_conclusion VARCHAR(20),
  next_due VARCHAR(20),
  file_path VARCHAR(500) NOT NULL,
  original_name VARCHAR(300) NOT NULL,
  file_role VARCHAR(10) DEFAULT '主件',  -- 主件/附件（docx 与 pdf 并存）
  size INTEGER,
  text_path VARCHAR(500),
  auto_type VARCHAR(20),
  auto_system VARCHAR(200),
  auto_year INTEGER,
  confidence INTEGER,                 -- 规则命中分，0~100
  confirmed INTEGER DEFAULT 0,
  remark TEXT,
  uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_net_reports_sys ON net_reports(system_id, doc_type, report_year);
CREATE INDEX ix_net_reports_no ON net_reports(project_no, report_year);

CREATE TABLE net_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id INTEGER REFERENCES net_reports(id),
  system_id INTEGER REFERENCES net_systems(id),
  problem_no INTEGER,                 -- 报告中的问题编号（1~N）
  security_class VARCHAR(30),         -- 安全类：安全区域边界/安全计算环境…
  control_point VARCHAR(30),          -- 控制点：身份鉴别/访问控制…
  target VARCHAR(300),                -- 测评对象/关联资产
  requirement TEXT,                   -- 测评项原文
  description TEXT NOT NULL,          -- 安全问题
  risk_level VARCHAR(10),             -- 高/中/低
  suggestion TEXT,                    -- 整改建议（8.1）
  source VARCHAR(20) DEFAULT 'report',-- report/prev_year/manual
  prev_status VARCHAR(20),            -- 前次整改情况：已整改/未整改
  owner VARCHAR(50),
  plan_date VARCHAR(20),
  done_date VARCHAR(20),
  status VARCHAR(20) DEFAULT '待整改', -- 待整改/整改中/已整改/已验收
  evidence_path VARCHAR(500),
  remark TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_net_findings_sys ON net_findings(system_id, status);
CREATE INDEX ix_net_findings_report ON net_findings(report_id);

CREATE TABLE net_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type VARCHAR(20) NOT NULL,   -- device/address/report/topology/system
  entity_id INTEGER,
  entity_label VARCHAR(200),
  field VARCHAR(50),
  old_value TEXT,
  new_value TEXT,
  reason VARCHAR(200),
  operator VARCHAR(50),
  ts DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_net_changes_entity ON net_changes(entity_type, entity_id);

CREATE TABLE net_import_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind VARCHAR(20) NOT NULL,          -- device/address/report
  file_name VARCHAR(300),
  total INTEGER, inserted INTEGER, updated INTEGER, skipped INTEGER,
  detail_json TEXT,                   -- 差异快照，用于回滚
  operator VARCHAR(50),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE net_dicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind VARCHAR(20) NOT NULL,          -- department/location/brand/purpose
  name VARCHAR(100) NOT NULL,
  alias VARCHAR(200),                 -- 别名，逗号分隔
  sort_order INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX ux_net_dicts ON net_dicts(kind, name);
```

### 4.3 关键设计取舍

1. **5 类设备共用一张表**：字段取并集，前端按 `category` 显示不同字段组（服务器/终端多 4 个字段，其他设备只显示用途）。
2. **一条记录 = 一台设备**；同型号可填 `quantity` 合并登记。导出调研表时按 `系统+设备名+品牌型号+区域` 聚合，备注自动生成"共 N 台（热备 M 台）"。
3. **IP/MAC 表不加唯一约束**：真实数据已有重复，唯一约束会阻断导入；冲突在应用层检测并高亮。
4. **设备管理 IP 允许为空**：调研表大量 `－`，强制必填会逼人造假。
5. **报告挂系统，不挂站点**：站点只是汇总视图，避免一个站点多系统时报告错位。

---

## 5. 页面设计（1 个导航页，7 个页签）

导航名：`🛰️ 网络通讯台账`

| 页签 | 内容 | 关键交互 |
|---|---|---|
| **① 总览** | 站点卡片（设备数/系统数/地址数/待办）；KPI；**2026 测评结果看板**（符合率排名、问题数、三级/二级分布）；等保合规矩阵；到期提醒；数据质量分 | 点卡片下钻；矩阵单元格点开看文件 |
| **② 站点与系统** | 站点列表（11）；系统列表（10）含等级/备案号/责任人；系统详情（设备数、拓扑图、报告、整改） | 编辑；系统下钻设备 |
| **③ 设备台账** | 筛选（站点/系统/大类/区域/重要性/状态/关键词）；表格；详情；新增/编辑/复制；批量修改；导入导出 | 一行展开看全字段；"复制新增"同型号 |
| **④ IP/MAC 台账** | 筛选（站点/部门/状态/关键词）；表格；冲突面板；异常清单；导入导出；部门/地点归一 | 冲突一键定位；MAC 一键标准化；按原模板导出 |
| **⑤ 拓扑图** | 按系统分组画廊；缩略图；当前版本/历史版本；源文件下载 | 上传自动按文件名匹配系统；设当前版本 |
| **⑥ 等保资料** | 合规矩阵；资料列表（系统/类型/年度/结论/符合率）；**一键上传自动识别**预览确认；**整改清单自动导入与跟踪** | 批量上传 → 预览表 → 改正 → 入库；报告详情页"生成整改清单" |
| **⑦ 报表/质量** | 调研表生成（按系统，5 张设备表 + 网络结构/外联/物理环境）；设备清单；IP-MAC 表；**年度测评对比表**；质量报告 | 一键导出 Excel；一键修复 |

界面沿用现有 Streamlit 风格：`st.container(border=True)` 卡片、`st.dataframe(on_select="rerun")` 选行、`st.tabs`、`st.expander` 详情、`st.form` 编辑。

---

## 6. 无 AI 的"智能"：规则引擎

### 6.1 测评报告自动识别（10/10 实测通过）

**A. 文件名即可判 90%**（10 份全部命中）：

```
NZSA202651-057-DB-ST-等保2.0测评报告-四川华电木里河水电开发有限公司-卡基娃电站计算机监控系统 - 盖章版.docx
└──── 项目编号 ────┘ └────────── 单位 ──────────┘ └──── 系统名 ────┘ └ 载体 ┘
```

正则：`^(NZSA[\d\-A-Z]+)-等保2\.0测评报告-(.+?)-(.+?)\s*-?\s*盖章版\.(docx|pdf)$`
→ 项目编号、单位、系统名、是否盖章版，一次取全。

**B. 正文只读 6 个固定位置**（章节定位，不做全文语义分析）：

| 字段 | 位置 | 规则 |
|---|---|---|
| 等级 | 2.1.1 定级结果后第一张表 | 取"安全保护等级 / 业务信息安全 / 系统服务安全"三列 |
| 测评周期 | 1.3 测评过程首段 | `从(\d{4})年(\d{1,2})月…至…(\d{4})年(\d{1,2})月…结束` |
| 测评项分布 | 第 6 章总体评价 | `共测评N项，其中符合项a项，部分符合项b项，不符合项c项，不适用项d项`（全文多处匹配，取 total 最大者） |
| 符合率 / 结论 | 第 7 章等级测评结论 | `符合率为(\d+(\.\d+)?)%`；`判定为(.{2,6})` |
| 测评机构 | 1.1 测评目的 | `委托(.{2,40}?)开展了网络安全等级保护测评` |
| 上次测评 | 附录 B 首段 | `于(\d{4})年(\d{1,2})月开展，综合得分为(\d+(\.\d+)?)，等级测评结论为(.{1,4})` |

**C. 置信度**：文件名命中 +60、系统匹配 +20、等级 +10、结论 +10；<80 标黄提示人工确认。

**D. 重复判断**：`项目编号 + 系统 + 年度 + 资料类型` 相同 → 提示"已存在"；docx 与 PDF 同名视为同一报告的两个载体，自动一主件一附件。

**E. 归档路径**：`uploads/net/reports/{系统编码}/{年份}/`，保留原始文件名。

**F. 识别结果（实测）**：10 份全部命中，系统名、等级、测评周期、符合率、结论、问题数均正确；`测评项` 初版正则误取分节小计（15 项），改为"取 total 最大者"后修正为 171/250/259。

### 6.2 拓扑图自动挂接

文件名匹配系统关键词（`集控`、`上通坝`、`卡基娃`、`俄公堡`、`立洲`、`绒达`、`当依`、`汇康`、`杜依`、`大丰`），同时含"接入点"→ 类型=接入点图。
上传后自动生成缩略图（Pillow，长边 1600px），列表秒开；原图点击查看。

### 6.3 IP/MAC 清洗与冲突

| 能力 | 规则 |
|---|---|
| MAC 标准化 | 去空格/换行 → 全角 `：` 转 `:` → 短横转 `:` → 大写 → 校验 12 位十六进制；失败保留原文并标记 |
| IP 校验 | 正则 + 私有地址段判断；`未接网线`/`固定IP` 等文本转入状态/备注，不占 IP 字段 |
| 重复检测 | 同 IP 多行=IP 冲突；同 MAC 多行=MAC 冲突；同 IP 同 MAC=完全重复 |
| 网段归属 | 按 IP 前缀归入站点（`10.168.21.*`→成都本部、`10.169.96.*`→现场），仅作分组，不强制 |
| 部门/地点归一 | 字典别名映射（`新能源 运管中心`→`新能源运管中心`；`成都办公楼`→`成都三楼办公楼`），可维护 |
| 一键修复 | 仅修复"格式类"问题（MAC 规范化、空格、全角）；语义类只提示不自动改 |

### 6.4 调研表一键生成（P1，价值最大）

按系统从台账反向填充 24 表模板：5 张设备表 + 网络结构 + 外联线路 + 物理环境 + 基本信息。
- 设备表按 `序号` 重排，`备注` 自动写台数；
- 系统名、IP、区域、物理环境直接取自台账；
- 输出继承原模板的合并单元格与列宽，测评机构可直接用。
> 这是"台账维护一次、测评少填一周"的核心卖点，建议放 P1 首位。

### 6.5 整改清单自动导入（P0.5，359 项一次生成）

**来源与字段**：

| 报告章节 | 表结构 | 提供字段 |
|---|---|---|
| 3.14 安全问题汇总 | 问题编号 / 安全问题 / 测评对象 / 通用扩展 / 安全类 / 控制点 / 测评项 | 问题主体 |
| 5.1 低中高风险分析 | 序号 / 安全类 / 安全问题 / 关联资产 / 关联威胁 / 危害分析 / 风险等级 | 风险等级 |
| 8.1 整改建议 | 序号 / 安全类 / 安全问题 / 关联资产 / 整改建议 | 整改建议 |
| 附录 B 前次整改 | 序号 / 安全问题 / 整改结果 / 情况说明 | 历史遗留 |

**解析要点**：

1. **跨页续表合并**：报告每页重复表头、编号为空的行，把文本追加到上一条的对应字段（实测 25~49 项/系统，去重后与 8.1 条数完全一致）。
2. **编号做主键**：3.14 的 `问题编号` ↔ 5.1 的 `序号` ↔ 8.1 的 `序号` 对齐，join 风险等级与整改建议。
3. **前次顺延**：附录 B 中"未整改"项 → `source=prev_year`、`prev_status=未整改`，自动进入本年清单。
4. **预览后入库**：勾选、改责任人、设期限，默认 `plan_date = 报告日期 + 90 天`。
5. **正文清洗**：报告存在大量断行与空格（"日 志""口 令"），入库前统一去空白；问题描述保留原文便于核对。

> 359 项手工录入约需 2~3 天，自动导入 + 人工确认约 30 分钟，这是本模块最直接的时间收益。

---

## 7. Excel 导入 / 双向同步

### 7.1 设备导入（直接吃调研表）

- 选择文件 → 自动识别工作表（网络设备/安全设备/服务器存储/终端设备/其他设备）；
- 自动定位表头行（第 3 行），按列名映射，不依赖列序；
- 系统名匹配 `net_systems`，未匹配的列出来让你选；
- 预览：新增 N / 更新 M / 跳过 K / 冲突行；确认后入库并记 `net_import_batches`。

### 7.2 设备双向同步

1. 导出当前台账（含隐藏 `ID` 列 + 全部字段）；
2. 线下修改；
3. 上传 → 按 `ID` 匹配更新、无 ID 新增、`ID` 不存在则跳过；
4. 差异预览（字段级旧值→新值）→ 勾选应用；
5. 生成变更日志 + 批次快照，可一键回滚。

### 7.3 地址台账导入/导出

- 兼容原表 7 列（序号/部门/姓名/办公电脑类型/使用地点/IP/MAC），也兼容整理后版本（多"是否安装网鉴V01"和备注）；
- 导出默认还原原表头，可直接发给各部门核对；
- 导入时按 `IP > MAC > 序号` 三级匹配，避免整表重录。

---

## 8. 待办与提醒（并入现有待办中心）

| 待办类别 | 触发条件 | 级别 |
|---|---|---|
| 等保测评到期 | `next_eval_date` 前 90/60/30 天 | 三级每年、二级每两年 |
| 整改逾期 | 整改项 `plan_date` 已过且未完成 | danger |
| 前次未整改顺延 | 附录 B 标记"未整改"且本年无对应整改记录 | warning |
| 报告缺失 | 系统 × 年度缺测评/自查 | warning |
| 备案证明缺失 | 系统有备案号但无备案证明文件 | warning |
| 台账待完善 | 设备缺系统/区域/责任人等关键字段 | info |
| IP/MAC 冲突 | 重复 IP、重复 MAC、格式异常 | danger/warning |
| 拓扑图待更新 | 当前图 `effective_date` 超 12 个月 | info |

沿用现有 `_generate_todo_items` + `AlertDismissed`，导航角标自动生效，不另做提醒系统。

---

## 9. 接口清单（约 33 个）

```
# 站点/系统
GET/POST/PUT/DELETE  /net/sites
GET/POST/PUT/DELETE  /net/systems
# 设备
GET/POST/PUT/DELETE  /net/devices
POST                 /net/devices/import        预览
POST                 /net/devices/import/apply  应用
GET                  /net/devices/export
POST                 /net/devices/batch-update
# 地址
GET/POST/PUT/DELETE  /net/addresses
POST                 /net/addresses/import
POST                 /net/addresses/import/apply
GET                  /net/addresses/export
GET                  /net/addresses/conflicts
POST                 /net/addresses/normalize
# 拓扑
GET/POST/DELETE      /net/topologies
GET                  /net/topologies/{id}/image
GET                  /net/topologies/{id}/download
# 等保资料
POST                 /net/reports/preview        上传后解析预览（不落库）
POST                 /net/reports/upload         多文件 + 自动识别 + 元数据入库
POST                 /net/reports/confirm
GET                  /net/reports
PUT/DELETE           /net/reports/{id}
GET                  /net/reports/{id}/download
POST                 /net/reports/{id}/extract-findings   解析 3.14/5.1/8.1/附录B
POST                 /net/findings/import/apply
GET                  /net/reports/summary         年度结果汇总（符合率/问题数/等级分布）
# 整改
GET/POST/PUT/DELETE  /net/findings
# 统计/质量
GET                  /net/overview
GET                  /net/compliance-matrix
GET                  /net/quality
GET                  /net/changes
POST                 /net/import-batches/{id}/rollback
GET                  /net/export/investigation-template
```

---

## 10. 权限、审计、备份、合规

- **权限**：单管理员，不做角色；每条记录写 `updated_by`，每次修改写 `net_changes`（字段级）。
- **审计**：现有中间件自动记录请求；报告下载额外记一条审计（谁在何时下载了哪份报告）。
- **备份**：文件放 `UPLOAD_DIR/net/...`，现有加密备份自动包含；数据库新增表随 `rd_platform.db` 一起备份。
- **合规**：不存设备口令/密钥，只存"凭据存放位置"；报告文件不对外网发送（无 AI，天然满足）。

---

## 11. 实施计划

| 阶段 | 内容 | 交付 |
|---|---|---|
| **P0**（1 个迭代） | 10 表建表 + 站点/系统/设备/地址 CRUD + Excel 导入导出 + 拓扑图上传预览 + **报告上传与 10 个元数据字段自动识别** + 总览 + 变更日志 + 质量报告 | 可日常维护的台账 |
| **P1** | **整改清单自动导入（359 项）+ 整改跟踪**、调研表反向生成、待办接入、合规矩阵、年度对比、批量修改、导入回滚、部门/地点归一 | 测评季直接出表/出清单 |
| **P2** | 设备二维码标签、端口级链路（如后续需要）、拓扑自动示意图、盘点任务 | 按需 |

代码落点：`models.py` 加 10 个模型；`main.py` 加 `/net/*` 路由；`app.py` 加 `render_net_page()` 与导航项；新增 `utils/net_rules.py`（文件名/章节/表格解析、清洗规则）、`assets/net/`（调研表模板）。报告解析复用 `utils/text_extract.py` 的 docx/PDF 文本提取。

---

## 12. 待确认事项

1. **让白光伏是 2026 新增系统**，我按"10 个系统 / 11 个站点节点"设计，是否确认？
2. **杜依**有站点、有地址，但没有定级系统和调研表——是并入当依系统，还是独立待定级？
3. **大丰、让白**的备案号与备案日期能否补一份？报告里没有备案号。
4. 10 份报告同时有 docx 和 PDF：默认"**PDF 主件 + docx 可检索附件**"，还是反过来？
5. 整改项默认期限 = 报告日期 + 90 天，是否合适（可改成 60/180 天或按项设置）？
6. 测评周期默认"三级每年、二级每两年"，是否按此设？
7. 设备"一条 = 一台，同型号可填数量，导出调研表自动汇总"——是否接受？

---

## 附录 A：数据质量问题清单（导入后首屏展示）

- 重复 IP 8 组：`10.169.96.10`、`.146`、`.147`、`.244`、`192.168.8.200`、`192.168.127.10`、`11.86.12.80`，另有 `未接网线` 2 条
- 重复 MAC 5 组：`1C-69-7A-2C-F3-09`、`1C-69-7A-2C-F2-CA`（3 次）、`E0-BE-03-95-E6-F1`、`70:66:55:5F:14:05`、`固定IP`（5 条文本）
- MAC 非标准：全角冒号 2 条、位数不足 1 条、`固定IP` 5 条
- IP 非地址：`未接网线` 2 条
- 部门别名：`新能源 运管中心`、`卡基娃`、`计划部`、`工程部`、`生技部`
- 地点别名：`成都办公楼`、`中控室`、`T20`

## 附录 B：设备导入列映射

| 调研表列 | 台账字段 |
|---|---|
| 所属系统 | `system_id`（名称匹配） |
| 设备名称 | `name` |
| 是否虚拟设备 | `is_virtual` |
| 系统及版本 / 操作系统及版本 | `os_version` |
| 品牌及型号 / 生产厂家及型号 | `brand_model` |
| 用途 / 设备类别/用途 | `purpose` |
| 重要程度 | `importance` |
| 备注 | `remark`（解析台数 → `quantity`） |
| 所属网络区域 | `zone` |
| 物理环境名称 | `room` |
| IP地址/掩码(/网关) | `mgmt_ip` |
| 是否热备 | `is_hot_standby` |
| 所属业务应用系统/平台名称 | `biz_app` |
| 数据库管理系统及版本 | `db_version` |
| 中间件及版本 | `middleware` |
| 安装杀毒软件名称及版本 | `antivirus` |

## 附录 C：测评报告字段提取对照（已在 10 份报告验证）

| 台账字段 | 报告位置 | 卡基娃 2026 实例 |
|---|---|---|
| `project_no` / `report_no` | 文件名前缀 | `NZSA202651-057-DB-ST` |
| `system_id` | 文件名 + 表 2-1 | 卡基娃电站计算机监控系统 |
| `level` / `level_s` / `level_a` | 表 2-1 定级结果 | 二级 / 二级 / 二级 |
| `eval_start` / `eval_end` | 1.3 测评过程首段 | 2026-06-22 ~ 2026-09-30 |
| `item_total/pass/partial/fail/na` | 第 6 章总体评价 | 171 / 124 / 17 / 7 / 23 |
| `pass_rate` | 第 7 章结论 | 83.78% |
| `conclusion` | 第 7 章结论 | 基本符合 |
| `problem_count` | 3.14 去重计数 | 25 |
| `risk_level` | 5.1 按问题序号 join | 高/中/低 |
| `suggestion` | 8.1 按问题序号 join | 整改建议原文 |
| `prev_status` | 附录 B | 未整改（已列入后续整改计划） |
| `last_eval_date/last_score/last_conclusion` | 附录 B 首段 | 2024-10 / 81.23 / 良 |
| `major_risk` | 首页"重大风险隐患及整改建议" | 无 |

**解析容错**：报告表格普遍跨页，下一页重复表头、编号为空；按"编号为空则追加到上一条"合并，再按编号去重。实测 3.14 与 8.1 条数完全一致（25/25），可作为解析正确性的自校验规则。

---

## 13. 落地状态（2026-10-28）

### 13.1 已实现（P0）

| 层 | 文件 | 内容 |
|---|---|---|
| 数据模型 | `models.py` | 新增 `net_sites / net_systems / net_devices / net_addresses / net_topologies / net_reports / net_findings / net_changes / net_import_batches / net_dicts` 10 张表 |
| 规则引擎 | `utils/net_rules.py` | 报告文件名/正文解析、整改清单抽取、MAC/IP 清洗、调研表与 IP-MAC 表导入映射、质量报告 |
| 后端接口 | `routers/network.py` | 约 33 个 `/net/*` 接口：站点/系统/设备/地址/拓扑/资料/整改/总览/合规矩阵/变更/导出 |
| 前端页面 | `net_ui.py` | 7 个页签：总览、站点与系统、设备台账、IP/MAC 台账、拓扑图、等保资料、导入导出 |
| 导航接入 | `app.py` | 侧边栏新增「🛰️ 网络通讯台账」；备份 `app.py.bak_pre_net` |

### 13.2 已导入真实数据

| 数据 | 数量 |
|---|---|
| 站点 | 10（集控、上通坝、卡基娃、俄公堡、立洲、大丰、绒达、当依、汇康、让白/杜依） |
| 定级系统 | 10 |
| 设备 | 342 条（3 套调研表） |
| IP/MAC | 197 条（重复 IP 7 组、重复 MAC 4 组，已在总览提示） |
| 等保资料 | 30 份（2026 测评报告 10 套 × docx+PDF = 20，备案证明 10） |
| 整改项 | 359 项（从 10 份报告 3.14/5.1/8.1 自动生成） |
| 拓扑图 | 13 张（含 6 张超大图已生成缩略图） |

2026 年测评：10 个系统全部“基本符合”，平均符合率 81.16%，问题合计 359 项。

### 13.3 验证结果

- 10 份报告元数据识别 10/10：系统、等级、测评周期、符合率、测评项分布、结论、上次得分均正确。
- 整改清单：卡基娃 25 项，与报告 8.1 条数一致；全部 10 套合计 359 项。
- 设备导入：3 套调研表 342 条写入；地址导入 197 条写入。
- 导出：设备台账、IP/MAC 台账、整改清单均返回 xlsx。
- 前端：Streamlit AppTest 真实渲染 7 个页签，无异常。

### 13.4 待办（P1）

- 调研表按系统反向生成（24 表模板回填）；
- 等保到期/整改逾期/IP 冲突接入现有待办中心；
- 设备批量修改 UI、导入批次回滚 UI；
- 部门/地点别名字典维护页；
- 年度测评对比图、二维码标签。

> 部署提示：当前开发会话启动的进程为低完整性级别，无法写入 `D:\sql_data`；正式使用请双击项目根目录的 `重启平台.bat`（先停旧进程，再按 start_all.bat 正常启动）。

### 13.5 调研表反向生成（已完成）

- 入口：「🛰️ 网络通讯台账 → 等保资料」页面底部「📄 调研表反向生成」。
- 模板：`assets/net/` 下 3 份公司模板（木里河 / 唐央 / 木里新能）。
- 范围：按公司（全部系统）或按单个系统。
- 刷新内容：
  - 信息系统基本情况：系统名称、S/A 等级、部署大区、备案号、扩展要求；
  - 5 类设备表：网络设备、安全设备、服务器存储设备、终端设备、其他设备。
- 保留内容：原模板的叙述性章节（单位情况、人员、物理环境、外联线路、网络结构、拓扑图、架构描述、软件平台、数据资源、文档、威胁、前次测评等），提交前需人工核对。
- 输出：25 个工作表（24 张原表 + 「台账数据说明」），文件名 `等保调研表_{范围}_{日期}.xlsx`。
- 实测：木里河 225 台（62/83/41/17/22）、唐央 43 台、新能 74 台；卡基娃单系统 42 台；信息系统描述/技术字段从模板保留，新增系统（大丰、让白）给出“请补充”占位。

### 13.6 部门/地点归一 + 数据质量闭环（已完成）

- 新增「数据质量」页签（8 个页签之一）。
- 字典：`net_dicts` 自动收录现有 22 个部门、54 个地点为规范项；预置 5 条部门别名（卡基娃→卡基娃电站、计划部→计划发展部、工程部→工程管理部、新能源 运管中心→新能源运管中心、集控→集控中心）。
- 待归一识别：按“原始值→规范值”列出，当前部门剩 3 条待归一（卡基娃 7 条、工程部 4 条、计划部 1 条），地点 0 条。
- 一键归一：接口 `POST /net/addresses/apply-dict`，支持 dry-run 预览；归一动作写 `net_changes` 留痕。
- 质量看板：地址完整度、重复 IP/MAC、缺 MAC/使用人/部门/地点、无效 IP、未归一项；设备缺系统/区域/物理环境；问题清单可下钻。
- 质量分公式：100 − 重复 IP×3 − 重复 MAC×3 − 缺 MAC×0.5 − 缺使用人×0.3 − 缺部门/地点×0.5 − 无效 IP×2 − 未归一×0.2/0.05。
- 一键修复：MAC 规范化/去空格、部门+地点全部归一、按类型归一。
- 实测：当前地址完整度 43%（重复 IP 7 组、重复 MAC 4 组、缺 MAC 15 条、缺部门 5 条、缺地点 3 条）；修复后分数会上升。
