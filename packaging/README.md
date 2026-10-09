# 科技项目管理系统 · Windows 桌面安装程序打包说明

本目录用于把「科技项目管理系统」打成 **Windows 桌面安装程序**（单机独立版、空库启动，
不附带任何现有业务数据）。

## 一、产出物与目录结构

| 文件 | 说明 |
|---|---|
| `build.bat` | 一键构建脚本（下载/安装 Python → 装依赖 → 拷贝源码 → 打 launcher → 编译安装包） |
| `installer.iss` | Inno Setup 6 安装脚本（中文界面、桌面/开始菜单快捷方式、卸载项） |
| `launcher.py` | 托盘启动器（单实例 → 首启向导 → 拉起后端/前端 → 开浏览器 → 托盘菜单） |
| `setup_wizard.py` | Tkinter 首启向导（DeepSeek Key 可空 + 管理员用户名/密码必填） |
| `launcher.spec` | PyInstaller spec（把 launcher.py 打成 windowed 单文件 exe） |
| `requirements-runtime.txt` | 运行时依赖（已剥离 QQ 机器人 / APScheduler / shapely / watchdog / pywin32） |
| `requirements-build.txt` | 构建期依赖（pystray / pyinstaller，不随运行包分发业务功能） |
| `exclude.lst` | 打包排除清单（数据库 / .env / 附件 / 备份 / 日志 / 科研资料 / 运维脚本） |
| `build_copy.py` | 按 exclude.lst 拷贝源码的辅助脚本 |
| `.env.example` | 环境配置模板（随安装包发布到安装根目录，供参考） |
| `README.md` | 本说明 |

安装后目录结构（目标机）：

```
{app}\                         # 默认 %LOCALAPPDATA%\Programs\科技项目管理系统
├─ 科技项目管理系统.exe         # 托盘启动器（双击即用）
├─ _packaged                   # 打包态标记（config 据此识别数据目录策略）
├─ setup_wizard.py             # 首启向导（由启动器拉起）
├─ .env.example                # 配置模板
├─ app\                        # 业务源码（只读）
│  ├─ main.py  app.py  config.py  models.py  database.py  security.py ...
│  ├─ routers\  utils\
├─ runtime\                    # 完整便携 Python（python.exe + site-packages）
└─ （数据不在此处）              # 见下节
```

## 二、数据目录（DATA_DIR）规则

数据（数据库 / `.env` / 上传文件 / 备份 / 聊天记录）统一放在：

```
%LOCALAPPDATA%\RDPlatform\data
```

优先级：

1. 环境变量 `RD_DATA_DIR`（便携 U 盘 / 自定义位置）
2. 安装目录存在 `_packaged` 标记 → `%LOCALAPPDATA%\RDPlatform\data`
3. 开发 / 旧部署 → 程序所在目录

因此 **卸载默认保留数据**；卸载向导会询问是否一并删除数据目录（默认「否」）。

## 三、构建步骤

前置条件：

- Windows 10/11 x64，能联网（首次构建需下载 Python 3.12.10）
- 已安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)（`ISCC.exe`）

步骤：

1. 双击（或在 cmd 中运行）`build.bat`
2. 等待完成（首次约 10~20 分钟，主要耗时在下载 Python 与安装依赖）
3. 产物：本目录下 `科技项目管理系统安装包.exe`

> 若已手动下载 `python-3.12.10-amd64.exe`，放到本目录后脚本会自动复用，不再联网下载。

## 四、替换应用图标

1. 准备一个 `.ico` 图标文件，命名为 `app.ico`，放到本目录。
2. 重新运行 `build.bat`。

`launcher.spec` 会在 `app.ico` 存在时把它打进 `科技项目管理系统.exe`；
`installer.iss` 会在 `app.ico` 存在时把它用作安装器图标。未放置 `app.ico` 时使用系统默认图标。

## 五、修改数据目录

默认 `%LOCALAPPDATA%\RDPlatform\data`。如需更改：

- 全局改：修改 `config.py` 的 `_resolve_data_dir()`（对应打包态分支）。
- 临时改：设置环境变量 `RD_DATA_DIR` 指向目标目录后启动。

## 六、卸载与数据保留

- 卸载入口：开始菜单「卸载 科技项目管理系统」或「设置 → 应用 → 卸载」。
- 卸载会删除 `{app}` 安装目录；数据目录默认**保留**，重新安装后可继续使用。
- 卸载向导最后会询问「是否同时删除用户数据目录」，选择「是」会删除数据库、上传文件、备份与配置（不可恢复）。

## 七、依赖精简说明

在项目根 `requirements.txt` 基础上，单机版移除了：

- `qq-botpy` + `aiohttp` 族（QQ 机器人，单机版已剥离）
- `APScheduler`（无使用，后台维护走纯 threading）
- `shapely`（无使用）、`watchdog`（Streamlit 热重载依赖）
- `pywin32`（仅 `.doc` 解析用，需目标机装 MS Word；`.docx` 走 python-docx 不受影响，`.doc` 自动降级提示）

**保留 OCR 三件套**（`opencv-python` / `onnxruntime` / `rapidocr-onnxruntime`），扫描件与图片文字识别功能完整可用。

## 八、注意事项

- 首次启动会弹出「首启配置向导」：DeepSeek API Key 可留空跳过，管理员密码必填。
- 后端/前端日志位于 `%LOCALAPPDATA%\RDPlatform\data\logs\`（backend.log / frontend.log / launcher.log）。
- 数据库为空库启动，`models.py` 在 import 时自动建表，无需额外迁移。
- 所有密钥（TOKEN_SECRET / BOT_TOKEN / BACKUP_KEY / ADMIN_PASSWORD_HASH）由 `security.py` / `audit_backup.py` 首次启动自动生成并写入 `DATA_DIR/.env`。
