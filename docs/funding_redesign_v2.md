# 经费管理模块 v2 设计说明

> 目标：把经费管理从「按项目×月份填格子」升级为 **「年度研发投入逐项核对」**：
> 合同分摊 → 财务单据 → 经费明细 → 年度归属，逐项比对、一键修复，确保每年研发投入准确。
>
> 本文对应实现：`models.py`、`main.py`、`funding_ui.py`、`app.py`（项目完结入口）。

---

## 1. 需求与设计目标

| 用户痛点 | v2 设计 |
|---|---|
| 两个项目可能共用一个合同，合同金额被重复计算 | 新增 **合同-项目分摊**（`contract_project_shares`）：一个合同可拆到多个项目，各项目只计自己的分摊额；未分完余额自动归主项目 |
| 缺乏修改功能 | 经费明细支持新增/修改/删除；财务单据可改金额/税额/日期/归属项目/合同；合同分摊可整体编辑；年度合同金额可人工覆盖 |
| 缺乏分类功能 | 明细增加 `category`（经费科目）与 `source`（经费来源）；类型仍为预算/到账/支出 |
| 缺乏查询功能 | 明细支持年度/项目/合同/类型/科目/来源/状态/关键词/日期区间筛选；矩阵支持口径切换；核对结果可导出 CSV |
| 新增项目完结 | `projects.completed` + `completed_at` + `completion_note`；完结项目自动不产生风险提醒，历史数据保留 |
| 需要逐项比对、一键化、保证年度投入正确 | 新增 **规则引擎核对**：单据↔台账↔合同分摊↔年度归属逐项匹配；一键修复可自动处理年度/月份、分类、合同关联、单据补录 |

---

## 2. 核心口径

1. **研发投入** = 当年 `fund_type='支出'` 的经费明细合计（预算、到账单列，不混入研发投入）。
2. **有单据支撑** = 明细与已确认财务单据按「合同 + 项目 + 年度 + 金额」逐项匹配成功。
3. **待补单据** = 支出明细中没有匹配到单据的金额。
4. **合同金额（项目口径）** = 合同分摊到该项目的金额，而不是合同全额。
5. **年度归属** = 优先用明细的 `year/month`；缺失时按 `fund_date` 推导；两者不一致会被核对引擎标出。
6. **共享合同单据** = 必须指定归属项目；未指定时不会自动摊入某个项目，而是在核对清单中提示。

---

## 3. 数据模型

### 3.1 `projects` 新增完结字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `completed` | INTEGER，默认 0 | 0=进行中，1=已完结 |
| `completed_at` | VARCHAR | 完结时间 |
| `completion_note` | TEXT | 完结说明/验收结论 |

完结后：
- `_generate_todo_items()` 统一过滤 `project_id in completed_ids` 的提醒；
- `_sync_todos()` 自动删除该项目已生成的待办；
- 合同统计的「已超期/即将到期」、数据大屏超期数同步排除；
- 经费、合同、成果等历史数据不删除，仍可查询。

### 3.2 `fundings` 升级为统一经费明细台账

新增字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `year` | INTEGER | 归属年度（索引） |
| `month` | INTEGER | 归属月份 1~12 |
| `category` | VARCHAR(50) | 经费科目：设备费/材料费/…/其他支出 |
| `source` | VARCHAR(50) | 经费来源：国拨/省拨/集团拨款/企业自筹/其他 |
| `contract_id` | INTEGER | 关联合同（索引） |
| `voucher_no` | VARCHAR(80) | 凭证号/单据号 |
| `status` | VARCHAR(10) | 已确认/待确认 |
| `legacy_cell_id` | INTEGER，唯一 | 旧 `funding_cells` 迁移幂等键 |
| `updated_at` | DATETIME | 最后修改时间 |

> 年度矩阵不再单独维护「单元格」作为唯一数据源，改为 `fundings` 的透视视图。

### 3.3 `contract_project_shares` 合同-项目分摊

| 字段 | 类型 | 说明 |
|---|---|---|
| `contract_id` | INTEGER | 合同 |
| `project_id` | INTEGER | 项目 |
| `amount` | FLOAT | 该项目分摊金额 |
| `ratio` | FLOAT | 分摊比例（辅助展示） |
| `remark` | VARCHAR | 备注 |

唯一约束：`(contract_id, project_id)`。

**分摊规则**：
- 没有分摊行 → 合同 100% 归 `contracts.project_id`（兼容历史数据）；
- 有分摊行 → 按 `amount` 计；`amount` 为空时按 `ratio × 合同含税金额`；
- 各分摊行合计小于合同总额 → 差额自动归主项目（页面提示「未分摊余额」）；
- 合计大于合同总额 → 保存时拒绝（400）；
- 项目合同的「合同总额/合同数」按分摊额计算，不再把共享合同全额计入每个项目。

### 3.4 `funding_attachments` 新增 `funding_id`

附件可精确挂到某条经费明细；旧附件仍按 `year + project_id + month` 关联。

### 3.5 旧数据迁移

- `funding_cells` 中金额 > 0 的行 → `fundings`，`fund_type='支出'`，`category='其他支出'`，`legacy_cell_id` 记录来源；
- 用 `system_configs.funding_cells_migrated_v2` 标记，迁移只执行一次；删除迁移后的明细不会被重启复活；
- 旧 `fundings.fund_date` 自动推导 `year/month`；
- 旧库通过 `ALTER TABLE` 加列，不重建表。

---

## 4. 逐项核对引擎（`GET /funding/reconcile`）

### 4.1 输入

- 年度 `year`；
- 可选 `project_id`（单项目核对）。

### 4.2 输出的三张表

1. **`annual_rows` 项目年度汇总**：合同分摊、预算、到账、支出（研发投入）、有单据支撑、待补单据、单据实际、差异、结论。
2. **`item_rows` 合同×项目逐项比对**：合同总额、本项目分摊、暂估/结算/发票、单据实际、台账支出、待关联金额、差异、状态。
3. **`issues` 问题清单**：级别、类型代码、涉及项目/合同/单据/明细、建议、是否可一键修复。

### 4.3 规则清单

| 代码 | 级别 | 含义 | 可一键修复 |
|---|---|---|---|
| `YEAR_MISSING` | warning | 明细没有年度/日期 | 有日期时可 |
| `MONTH_MISSING` | info | 明细缺月份 | 是 |
| `PERIOD_MISMATCH` | warning | 年度/月份与发生日期不一致 | 是 |
| `CATEGORY_MISSING` | info | 缺经费科目 | 是（补「其他支出」） |
| `SOURCE_MISSING` | info | 缺经费来源 | 是（补「其他」） |
| `AMOUNT_INVALID` | danger | 金额 ≤ 0 | 否 |
| `ENTRY_NO_CONTRACT` | info | 台账未关联合同 | 能按单据匹配时可 |
| `ENTRY_NO_DOC` | warning | 台账支出无对应单据 | 否 |
| `CONTRACT_MISMATCH` | warning | 台账与单据关联合同不一致 | 否 |
| `DOC_NOT_BOOKED` | danger | 已确认单据未入台账 | 是（生成台账草稿） |
| `DOC_DATE_MISSING` | warning | 单据缺日期 | 否 |
| `SHARED_DOC_NO_PROJECT` | warning | 共享合同单据未指定项目 | 否 |
| `SHARE_OVER` | danger | 分摊合计超合同总额 | 否 |
| `SHARE_REMAINDER` | info | 分摊未分完，余额归主项目 | 否 |
| `LEDGER_OVER_SHARE` | warning | 项目台账超合同分摊额 | 否 |
| `DUPLICATE_CONTRACT` | warning | 同编号或同名同额，疑似重复合同 | 否 |

### 4.4 匹配算法（避免重复补录）

1. 先按「合同 + 项目 + 年度 + 金额（两位小数）」精确匹配单据与支出明细；
2. 精确匹配不到时，再按「项目 + 年度 + 金额」兜底匹配，并给出「台账/单据关联合同不一致」提示；
3. 只有两次都匹配不到的单据才判定为 `DOC_NOT_BOOKED`；
4. 可兜底匹配的明细标记为 `ENTRY_NO_CONTRACT(auto_fixable)`，一键修复时按单据补合同，不会新建重复明细；
5. 补录前再执行一次核对，已能匹配的单据不会重复生成。

### 4.5 一键修复（`POST /funding/reconcile/apply`）

可勾选四类动作：

| 动作 | 行为 |
|---|---|
| `fix_period` | 按 `fund_date` 修正年度/月份 |
| `fill_classification` | 缺科目补「其他支出」，缺来源补「其他」 |
| `link_contract_from_docs` | 按匹配到的单据补台账 `contract_id` |
| `create_entries_from_docs` | 为真正未入账的单据生成支出明细（`voucher_no=DOC-{id}` 防重复） |

所有动作写审计日志，返回各类修正条数。

---

## 5. 后端 API（v2 新增/增强）

### 经费明细
- `GET /funding/entries`：年度/项目/合同/类型/科目/来源/状态/关键词/日期区间筛选 + 汇总 + 年度列表
- `POST /funding/entries`：新增
- `GET /funding/entries/{id}`：详情 + 附件
- `PUT /funding/entries/{id}`：修改（`PUT /fundings/{id}` 兼容）
- `DELETE /funding/entries/{id}`：删除（`DELETE /fundings/{id}` 兼容）
- `GET /funding/meta`：项目、合同分摊、科目、来源、类型、年度
- `GET /funding/pivot`：年度矩阵（项目×月份，数据源为明细）
- `GET /funding-grid/summary`：项目全周期跨年汇总（数据源改为明细）

### 合同分摊
- `GET /funding/contracts`：合同分摊总览
- `GET /contracts/{id}/shares`：查看某合同分摊
- `PUT /contracts/{id}/shares`：整体保存分摊（校验重复项目、负数、超额）

### 核对
- `GET /funding/reconcile?year=&project_id=`：逐项核对
- `GET /funding/annual?year=`：年度研发投入汇总
- `POST /funding/reconcile/apply`：一键修复

### 项目完结
- `POST /projects/{id}/complete`：完结（可带说明）
- `POST /projects/{id}/reopen`：重新开启
- `GET /projects/{id}/dashboard`：新增 `completed/completed_at/completion_note`、`funding_expense`

### 兼容旧接口
- `GET/POST /fundings`、`GET /funding-grid`、`POST /funding-grid/cells`、附件旧路径均保留；
- 旧「单元格保存」会写入/更新一条经费明细，保证旧调用不丢数据。

---

## 6. 前端页面（`funding_ui.py`）

顶部统一「核对年度」，七个 tab：

1. **🎯 年度核对**
   - 6 个 KPI：研发投入、有单据支撑、待补单据、台账 vs 单据差异、项目数、问题/可修复数；
   - 一键智能核对、一键修复（四类动作）、导出 CSV；
   - 三张视图：项目年度汇总 / 合同逐项比对 / 问题清单；
   - 历年研发投入趋势图。
2. **📒 经费明细**：筛选 + 汇总 + 明细表 + 新增/编辑/删除 + 附件。
3. **📅 年度矩阵**：项目×月份透视、合同年度金额人工修正、单元格明细/快速补录、项目行管理。
4. **🤝 合同分摊**：合同分摊总览 + `data_editor` 动态编辑 + 实时未分摊余额/超额校验。
5. **🧾 财务单据**：按项目/按合同上传、共享合同指定归属项目、校对/确认/删除/下载。
6. **⚠️ 风险预警**：仅显示经费/合同类提醒，已完结项目自动隐藏；单条不再提醒、批量清理。
7. **⚙️ 口径设置**：最终金额口径、超额基准、容忍比例；核对口径说明。

项目列表与项目管理页：
- 项目列表显示「✅ 已完结 / 🟢 进行中」，每行可直接「✅ 完结」或「↩️ 开启」；
- 完结前有说明/验收结论输入与二次确认；
- 项目管理页信息卡显示完结状态、完结时间、完结说明，并可重新开启。

---

## 7. 迁移与兼容

- 启动时 `Base.metadata.create_all` 建新表，`ALTER TABLE` 加列；
- 旧 `funding_cells` 一次性迁移，标记 `funding_cells_migrated_v2`；
- 旧 `funding-grid` / `fundings` API 保留；
- 旧项目 `completed` 统一回填 0；
- 旧合同没有分摊行时按主项目 100% 计算，行为与升级前一致。

---

## 8. 验证记录（2026-11-01）

- `python -m py_compile main.py models.py app.py funding_ui.py`：通过。
- 临时后端 8002 接口：
  - `/funding/meta`、`/funding/pivot?year=2026`、`/funding/entries?year=2026`、`/funding/reconcile?year=2026`、`/funding/contracts`、`/contracts?project_id=17` 均 200；
  - 迁移 2 条旧单元格 → 经费明细，项目 17 / 16 金额正确；
  - 合同 32 分摊到 17/16 后，`/contracts?project_id=16` 能查到共享合同；向未分摊项目 15 记明细返回 400；向分摊项目 16 记明细成功；
  - 一键修复：`linked_contracts=1` 把项目 17 的暂估明细关联到合同 32；核对差异从 2,855,274 降为 0，状态「一致」；
  - 项目 18 完结后，合同类预警从 2 条降为 0（总 14→12）；重新开启后恢复 2 条（总 12→14）。
- Streamlit `AppTest`（后端 8002）：
  - 经费管理页 7 个 tab、0 异常；
  - 新增明细表单打开/保存成功（创建后已清理测试数据）；
  - 合同分摊 `data_editor` 保存成功（保存后已重置）；
  - 项目列表与项目管理页 0 异常，完结确认界面正常。

### 已知边界

- 「研发投入」口径固定为支出类明细；如财务口径要求包含预算/到账，可在「口径设置」外再扩展。
- 单据↔台账匹配按金额精确到分；金额被人工改动过大会判为不一致，需要人工确认。
- 共享合同的单据必须指定项目；未指定时只提示，不自动摊分。
- 重复合同只提示，不自动合并（避免误删），请走「合同管理 → 查重去重」。
