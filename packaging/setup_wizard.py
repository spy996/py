# -*- coding: utf-8 -*-
"""科技项目管理系统 · 首启配置向导（Tkinter，标准库，无第三方依赖）。

职责：收集 DeepSeek API Key（可留空）+ 管理员用户名（默认 admin）+ 管理员密码（必填），
写入 DATA_DIR/.env。后端首次启动时 security.py 会把明文 ADMIN_PASSWORD 自动哈希为
ADMIN_PASSWORD_HASH 并从 .env 删除明文。

由 launcher.py 用便携 runtime\\python.exe 以子进程方式拉起，退出码：
  0 = 成功写入（或已具备密码配置，幂等跳过）
  1 = 用户取消 / 未完成配置
  2 = 参数/环境错误
"""
import os
import sys
import argparse

DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_USERNAME = "admin"

# 写入 .env 时会整体替换的键（保证新旧密码/Key 不残留、明文密码最终被哈希接管）
_REPLACE_KEYS = {
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_URL",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "ADMIN_PASSWORD_HASH",
}


def _resolve_data_dir(script_dir: str) -> str:
    """与 config._resolve_data_dir 保持一致的数据目录解析逻辑。"""
    env = os.getenv("RD_DATA_DIR")
    if env:
        return env
    if os.path.exists(os.path.join(script_dir, "_packaged")):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "RDPlatform", "data")
    return script_dir


def _read_lines(env_path: str):
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            return f.readlines()
    return []


def _has_password_config(env_path: str) -> bool:
    """.env 里是否已存在非空的管理员密码（明文或哈希）。"""
    for ln in _read_lines(env_path):
        s = ln.strip()
        if s.startswith("ADMIN_PASSWORD_HASH=") or s.startswith("ADMIN_PASSWORD="):
            if s.split("=", 1)[1].strip():
                return True
    return False


def _write_env(env_path: str, api_key: str, username: str, password: str) -> None:
    """写入/更新 .env，保留其它既有键（TOKEN_SECRET/BOT_TOKEN/BACKUP_KEY 等）。"""
    kept = [ln for ln in _read_lines(env_path)
            if not any(ln.strip().startswith(k + "=") for k in _REPLACE_KEYS)]
    # 去掉尾部多余的连续空行，保证追加内容整齐
    while kept and not kept[-1].strip():
        kept.pop()
    if kept and not kept[-1].endswith("\n"):
        kept[-1] = kept[-1] + "\n"
    kept.append("\n")
    kept.append("# ---- 由首启向导写入 ----\n")
    kept.append("DEEPSEEK_API_KEY=%s\n" % api_key.strip())
    kept.append("DEEPSEEK_API_URL=%s\n" % DEFAULT_DEEPSEEK_URL)
    kept.append("ADMIN_USERNAME=%s\n" % username.strip())
    kept.append("ADMIN_PASSWORD=%s\n" % password)
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(kept)


def run_wizard(env_path: str) -> bool:
    """弹 Tkinter 窗口收集配置，返回是否成功写入。"""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("科技项目管理系统 · 首次配置")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    pad = {"padx": 12, "pady": 6}
    row = 0

    tk.Label(root, text="欢迎使用科技项目管理系统", font=("Microsoft YaHei", 13, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", **pad)
    row += 1
    tk.Label(root, text="首次启动前，请完成以下配置。DeepSeek Key 可留空跳过。",
             fg="#555555").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
    row += 1

    tk.Label(root, text="DeepSeek API Key（可留空）").grid(row=row, column=0, sticky="w", **pad)
    api_var = tk.StringVar(value="")
    api_entry = tk.Entry(root, textvariable=api_var, width=42, show="*")
    api_entry.grid(row=row, column=1, sticky="we", **pad)
    row += 1

    tk.Label(root, text="管理员用户名").grid(row=row, column=0, sticky="w", **pad)
    user_var = tk.StringVar(value=DEFAULT_USERNAME)
    tk.Entry(root, textvariable=user_var, width=42).grid(row=row, column=1, sticky="we", **pad)
    row += 1

    tk.Label(root, text="管理员密码（必填）").grid(row=row, column=0, sticky="w", **pad)
    pwd_var = tk.StringVar(value="")
    tk.Entry(root, textvariable=pwd_var, width=42, show="*").grid(row=row, column=1, sticky="we", **pad)
    row += 1

    tk.Label(root, text="确认密码").grid(row=row, column=0, sticky="w", **pad)
    confirm_var = tk.StringVar(value="")
    tk.Entry(root, textvariable=confirm_var, width=42, show="*").grid(row=row, column=1, sticky="we", **pad)
    row += 1

    result = {"ok": False}

    def on_confirm():
        username = user_var.get().strip() or DEFAULT_USERNAME
        pwd = pwd_var.get()
        pwd2 = confirm_var.get()
        if not pwd:
            messagebox.showerror("提示", "管理员密码不能为空。", parent=root)
            return
        if pwd != pwd2:
            messagebox.showerror("提示", "两次输入的密码不一致，请重新输入。", parent=root)
            return
        if len(pwd) < 8:
            messagebox.showwarning("提示", "密码长度建议至少 8 位，本向导仍将写入。", parent=root)
        try:
            _write_env(env_path, api_var.get(), username, pwd)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("错误", "写入 .env 失败：%s" % e, parent=root)
            return
        result["ok"] = True
        root.destroy()

    def on_cancel():
        result["ok"] = False
        root.destroy()

    btn = tk.Frame(root)
    btn.grid(row=row, column=0, columnspan=2, sticky="e", **pad)
    tk.Button(btn, text="完成并保存", command=on_confirm, width=14).pack(side="left", padx=6)
    tk.Button(btn, text="取消", command=on_cancel, width=10).pack(side="left", padx=6)
    row += 1

    # 关闭窗口（右上角 X）等价于取消
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    # 居中显示
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, (sh - h) // 2)))

    root.mainloop()
    return result["ok"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="科技项目管理系统 · 首启配置向导")
    parser.add_argument("--data-dir", default=None, help="数据目录（覆盖默认解析，通常由 launcher 传入）")
    args = parser.parse_args(argv)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = args.data_dir or _resolve_data_dir(script_dir)
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as e:
        print("[错误] 无法创建数据目录 %s：%s" % (data_dir, e), file=sys.stderr)
        return 2

    env_path = os.path.join(data_dir, ".env")
    if _has_password_config(env_path):
        print("[配置] 已存在管理员密码配置，跳过向导。")
        return 0

    if "DISPLAY" not in os.environ and os.name != "nt":
        print("[错误] 无图形界面，无法运行首启向导。", file=sys.stderr)
        return 2

    try:
        ok = run_wizard(env_path)
    except Exception as e:  # noqa: BLE001
        print("[错误] 向导运行异常：%s" % e, file=sys.stderr)
        return 2

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
