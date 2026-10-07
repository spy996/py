# 单文件拆分路线图（main.py / app.py）

> 目标：把后端 main.py（约 5,562 行）与前端 app.py（约 3,286 行）逐步拆分为
> 模块化目录结构，降低维护成本。**原则：小步走、每步可验证、每步可回退，不一次性硬拆。**

---

## 0. 现状盘点

### main.py（后端 FastAPI + SQLAlchemy + SQLite）

| 行号区间 | 内容 |
| --- | --- |
| 1–60 | 导入 + OCR 引擎懒加载（`_get_ocr_engine`） |
| 61–220 | 配置（.env 加载）+ 日志 + 认证安全（PBKDF2 / token 生成与校验） |
| 222–640 | 数据模型（18 个 `class Xxx(Base)`） |
| 641–792 | 审计日志 + 自动加密备份 + 归档 + 后台维护循环 |
| 793–1084 | 文本提取（PDF/OCR/Excel/Docx/PPT/图片） |
| 1085–1120 | 磁盘文本存储辅助（`_content_path` / `_save_content` / `_load_content`） |
| 1121–1352 | 智能问答检索 + DeepSeek 调用 |
| 1353–1400 | `app = FastAPI(...)` + 认证中间件 + `get_db` |
| 1402–1706 | 认证 / 审计 / 备份接口 + 项目 CRUD + 项目聚合 dashboard |
| 1707–1924 | 文件上传 / 下载 / 内容解析 |
| 1925–2003 | 全文搜索 + 健康检查 + 智能问答接口 |
| 2004–2656 | 摘要 / 合同字段提取 / 智能分类 / 项目统计与总结 |
| 2657–2846 | 跨项目统计与报告 |
| 2847–3767 | 合同管理（字段提取 / 执行节点 / 查重去重） |
| 3768–4220 | 经费 + 年度经费一览表 + 财务资料 |
| 4440–4903 | 系统配置 + 经费比对 / 预警 / 待办 / 预算 / 跨年汇总 |
| 5091–5434 | 成果台账 + 提醒中心 |
| 5488 | 数据大屏聚合 `/dashboard` |

### app.py（前端 Streamlit 单页）

| 行号区间 | 内容 |
| --- | --- |
| 1–27 | 导入 |
| 28–167 | `set_page_config` + 全局 CSS 样式 |
| 168–255 | 登录认证 + 审计 / 备份 API |
| 256–554 | 经费 / 成果 / 提醒 / 大屏 / 项目 / 文件 API |
| 555–895 | 合同 / 财务资料 / 配置 / 预警 / 待办 API |
| 896–1042 | 辅助函数 + 问答 / 搜索 API |
| 1043–1597 | `render_ask_page` + `render_contract_page` |
| 1598–3286 | 页面主体（侧边栏导航 + 11 个页面渲染） |

---

## 1. 目标目录结构

```
科技项目管理系统/
├─ main.py                  # 仅保留入口：创建 app、挂载 router、启动维护循环
├─ app.py                   # 仅保留入口：登录 + 导航 + 页面路由
├─ constants.py             # 已有（阶段枚举等，保持）
├─ ui_common.py             # 已有（前端纯函数，保持）
│
├─ backend/                 # 后端包（或直接平铺为 *.py，见第 4 节）
│  ├─ __init__.py
│  ├─ config.py             # .env 加载
│  ├─ database.py           # engine / SessionLocal / Base / get_db
│  ├─ models.py             # 18 个 SQLAlchemy 模型
│  ├─ security.py           # 密码哈希 / token / 认证依赖
│  ├─ utils/
│  │  ├─ text_extract.py    # PDF/OCR/Excel/Docx 提取 + OCR 懒加载
│  │  ├─ storage.py         # 磁盘文本存储辅助
│  │  ├─ audit_backup.py    # 审计 + 备份 + 归档 + 维护循环
│  │  └─ ai.py              # DeepSeek 问答 / 摘要 / 分类 / 跨项目
│  └─ routers/
│     ├─ auth.py            # /auth/* /audit/* /backup/*
│     ├─ projects.py        # /projects/*（含项目聚合 dashboard）
│     ├─ files.py           # /files/* 解析/搜索/摘要
│     ├─ ask.py             # /ask /search
│     ├─ contracts.py       # /contracts/* /contract-nodes/*
│     ├─ fundings.py        # /fundings/* /funding-grid/* /financial-docs/*
│     ├─ config.py          # /config /funding/* 比对/预警/待办/预算
│     ├─ achievements.py    # /achievements/* /reminders
│     └─ dashboard.py       # /dashboard
│
└─ frontend/                # 前端包
   ├─ __init__.py
   ├─ config_ui.py          # set_page_config + 全局样式
   ├─ api_client.py         # 全部 api_* 函数（HTTP 封装）
   ├─ components/
   │  ├─ common.py          # _make_summary / _file_type / _format_date / _highlight_keyword
   │  └─ charts.py          # _status_donut_svg 等图表
   └─ pages/
      ├─ projects.py        # 项目列表 + 创建项目
      ├─ files.py           # 文件管理
      ├─ project_dashboard.py
      ├─ contracts.py       # 合同管理（含 render_contract_page）
      ├─ fundings.py        # 经费管理
      ├─ achievements.py    # 成果台账
      ├─ search.py          # 全文搜索
      ├─ ask.py             # 智能问答（含 render_ask_page）
      ├─ cross_project.py   # 跨项目分析
      ├─ audit.py           # 审计日志
      ├─ backup.py          # 系统备份
      ├─ reminders.py       # 提醒中心
      └─ dashboard.py       # 数据大屏
```

> 说明：`backend/`、`frontend/` 包名仅作示意。**建议用平铺目录
> `routers/`、`services/`、`models/` 而非 `backend/` 包**，可减少 import 路径改动量。
> 最终形态在 Stage 0 评审时按团队习惯敲定。

---

## 2. 分阶段执行计划

> 每阶段结束都必须：`python -m py_compile` 全量通过 + `import main` 通过 + 跑一遍冒烟脚本 +
> `git commit`。任何阶段失败，仅回退该阶段改动，不影响已上线功能。

### Stage 0 — 基线锁定（安全网，约 0.5 小时）

- 把当前「能跑」的版本打一个基线 commit / tag（如 `v1.0-baseline`）。
- 复用 QA 的 26 项冒烟断言，固化为 `scripts/smoke_test.py`，作为每阶段的回归门禁。
- 产出：可一键回归的基线。**没有这一层，后续每一步都是裸奔。**

### Stage 1 — 后端纯函数抽离（零风险，约 1–2 小时）

只抽**无状态、不依赖路由**的纯函数，路由代码一行不动：

- `utils/text_extract.py`：`_extract_text_from_pdf`、`_pdf_needs_ocr`、`_extract_text_from_excel`、
  `_extract_text_from_xls`、`_extract_text_from_docx`、`_extract_text_from_pptx`、
  `_extract_text_from_doc`、`_extract_text_from_image`、`extract_text_from_file`、
  `is_supported_file` + OCR 懒加载 `_get_ocr_engine`。
- `utils/storage.py`：`_content_path`、`_save_content`、`_load_content`。
- `utils/ai.py`：`_call_deepseek`、`_call_deepseek_summary`、`_call_deepseek_cross_project`、
  `_chunk_text`、`_retrieve_context`、`_fallback_terms`、`_expand_query`。
- main.py 顶部改为 `from utils.text_extract import ...`，调用点逐个替换。

**风险点**：这些函数引用了模块级全局（如 OCR 引擎、DeepSeek key、`extract_text_from_file`
内部的阈值）。抽离时把依赖显式作为参数或从 `config.py` 传入，避免循环 import。

### Stage 2 — 模型 + DB + 安全层抽离（中风险，约 1–2 小时）

- `models.py`：18 个模型类原样搬移（注意 `Base`、`relationship`、外键字符串引用不变）。
- `database.py`：`engine`、`SessionLocal`、`Base`、`get_db`。
- `security.py`：`_pbkdf2_hash`、`_verify_password`、`_make_token`、`_verify_token`、`_pwd_version`。
- `config.py`：`_load_env`、`_append_env`、`_remove_env`。
- main.py 改为 `from models import Project, ProjectFile, ...`、`from database import engine, SessionLocal, get_db`。

**风险点**：
- SQLAlchemy 模型之间 `relationship` 若用了字符串类名，搬移后仍生效；若用了运行时对象需一并调整。
- 旧库迁移（`ALTER TABLE`）逻辑若依赖 `inspect(engine)`，务必保留在 `database.py` 或
  `models.py` 的迁移函数里，别在抽离中弄丢。

### Stage 3 — 后端路由拆分（中高风险，约 3–4 小时，**逐个迁移**）

- 每个文件用 `APIRouter(prefix=..., tags=[...])` 包装；main.py 里 `app.include_router(...)`。
- 共享依赖（`get_db`、认证中间件）从 `database.py` / `security.py` 导入。
- **迁移顺序按依赖从少到多**，每迁一个验证一个：
  1. `auth.py`（认证 + 审计 + 备份，几乎无跨模块依赖）
  2. `projects.py`（项目 CRUD + 项目聚合）
  3. `files.py`（文件上传下载 + 解析 + 搜索 + 摘要）
  4. `ask.py`（智能问答 + 全文搜索）
  5. `contracts.py`（合同全量）
  6. `fundings.py`（经费 + 一览表 + 财务资料）
  7. `config.py`（系统配置 + 比对 / 预警 / 待办 / 预算）
  8. `achievements.py`（成果 + 提醒中心）
  9. `dashboard.py`（数据大屏）

**风险点**：
- Pydantic 请求体模型（`ProjectCreate`、`ContractUpdateRequest` 等）散落在各处，抽离时
  跟对应 router 放一起，避免重复定义。
- 跨模块复用函数（如合同字段提取被财务资料、经费比对调用）应下沉到 `services/` 或
  `utils/`，避免 router 之间互相 import。

### Stage 4 — 前端 API 层 + 组件抽离（低风险，约 1–2 小时）

- `api_client.py`：把 app.py 里所有 `api_*` 函数（约 70 个）原样搬移，统一封装
  `requests` 与 token 头。app.py 只 `from api_client import ...`。
- `components/common.py`：`_make_summary`、`_file_type`、`_format_date`、`_highlight_keyword`。
- `components/charts.py`：`_status_donut_svg` 等图表生成函数。

**风险点**：`api_*` 函数依赖 `st.session_state` 里的 token / `st.sidebar` 提示。
抽离时让 `api_client` 通过 `st.session_state` 读 token（Streamlit 全局可访问），
避免改调用签名。

### Stage 5 — 前端页面拆分（中风险，约 2–3 小时，**逐个迁移**）

- 每个页面封装为 `def page_xxx(): ...`，放 `pages/` 目录；app.py 侧边栏按选中项调用。
- Streamlit 页面函数内可直接调用 `st.xxx`，无需传上下文，因此纯搬移即可，逻辑基本不变。
- 迁移顺序：项目 → 文件 → 项目管理 → 合同 → 经费 → 成果 → 搜索 → 问答 → 跨项目 →
  审计 → 备份 → 提醒 → 大屏。每迁一个，`streamlit run app.py` 点一遍该页。

**风险点**：
- 页面间共享的 `st.session_state` 键名要统一登记，避免跨文件重名冲突。
- 顶部全局样式与 `set_page_config` 必须保持在 app.py 最先执行，不能搬进子页面。

### Stage 6 — 收敛与清理（约 1 小时）

- 删除 main.py / app.py 中的死代码与残留注释块。
- 补 `__init__.py`、统一 import 风格、更新 README 与 `使用说明.md` 的启动说明。
- 全量回归 + `git commit`。

---

## 3. 风险与回退策略

| 风险 | 缓解 |
| --- | --- |
| 拆坏能跑的系统 | Stage 0 基线 + 每阶段独立 commit，失败即 `git revert` 单阶段 |
| 循环 import | 依赖方向固定：router → service → model/db；禁止 router 间互 import |
| SQLAlchemy 关系断裂 | 保持模型类名与关系字符串不变；拆完跑一次全表 CRUD 冒烟 |
| 旧库 ALTER TABLE 迁移丢失 | 迁移逻辑集中到 `database.py`，Stage 2 单独回归验证 |
| Streamlit 全局状态错乱 | 页面函数不改 session_state 键名；`set_page_config` 留在入口 |

---

## 4. 建议

- **优先做 Stage 0–1–4**（纯抽离、零逻辑改动），收益立竿见影且风险极低。
- Stage 3 是最大工作量，务必按「一个 router 一个 commit」推进。
- 每阶段用同一份冒烟脚本当门禁，避免「拆完了但悄悄坏掉」。
