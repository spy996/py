# -*- coding: utf-8 -*-
"""构建辅助：把项目源码拷贝到安装包暂存目录，按 exclude.lst 排除业务数据/运维文件。

用途：build.bat 调用本脚本，保证「只拷贝源码与配置模板、绝不打包现有业务数据」。
"""
import os
import sys
import shutil
import fnmatch
import argparse

# 这些目录永远不拷贝（无论 exclude.lst 是否列出）
ALWAYS_SKIP_DIRS = {
    ".git",
    ".workbuddy",
    ".venv",
    "venv",
    "__pycache__",
    "packaging",   # 打包工程自身不进 app 源码目录
    "docs",        # 设计文档不随安装包发布
}


def load_exclude(path: str):
    """读取排除清单，返回 (目录模式列表, 文件模式列表)。"""
    dir_pats = []
    file_pats = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.endswith("/"):
                    dir_pats.append(line[:-1])
                else:
                    file_pats.append(line)
    return dir_pats, file_pats


def _match(patterns, name, relpath):
    """patterns 任一匹配文件名或相对路径即返回 True。"""
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(relpath, p) for p in patterns)


def copy_tree(src: str, dst: str, dir_pats, file_pats) -> int:
    """把 src 下的文件拷贝到 dst，跳过排除项，返回拷贝文件数。"""
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    count = 0
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        rel_root = "" if rel_root == "." else rel_root

        # 过滤子目录（原地修改 dirs 即可让 os.walk 不再深入）
        kept_dirs = []
        for d in dirs:
            if d in ALWAYS_SKIP_DIRS:
                continue
            rel_d = os.path.join(rel_root, d).replace(os.sep, "/") if rel_root else d
            # 目录名同时匹配「目录模式」与「文件模式」：例如目录名以 .ref 结尾
            # （.专利清单-按年度分类.ref）应命中 file_pats 里的 *.ref，整目录排除。
            if _match(dir_pats, d, rel_d) or _match(file_pats, d, rel_d):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        dest_root = os.path.join(dst, rel_root) if rel_root else dst
        os.makedirs(dest_root, exist_ok=True)

        for fn in files:
            rel_f = os.path.join(rel_root, fn).replace(os.sep, "/") if rel_root else fn
            if _match(file_pats, fn, rel_f):
                continue
            shutil.copy2(os.path.join(root, fn), os.path.join(dest_root, fn))
            count += 1
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按排除清单拷贝项目源码")
    parser.add_argument("--src", required=True, help="源码根目录")
    parser.add_argument("--dst", required=True, help="目标目录")
    parser.add_argument("--exclude", default="exclude.lst", help="排除清单文件")
    args = parser.parse_args(argv)

    dir_pats, file_pats = load_exclude(args.exclude)
    if os.path.exists(args.dst):
        shutil.rmtree(args.dst)
    os.makedirs(args.dst, exist_ok=True)
    count = copy_tree(args.src, args.dst, dir_pats, file_pats)
    print(f"[构建] 已拷贝 {count} 个源文件：{args.src} -> {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
