#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arcadia_Task 依赖自检/安装工具（拉取仓库后执行一次即可）。

作用：
    扫描 ZWZW/ 下的签到脚本，找出它们 import 的第三方依赖；检测到未安装的就自动
    pip install。脚本本身也带"运行时自举"（首次用到 curl_cffi 时自动安装），
    本工具用于提前批量装好，避免第一次跑签到任务时卡在安装上。

用法：
    python tools/install_deps.py                # 检测，缺失时询问是否安装
    python tools/install_deps.py --yes          # 直接安装，不询问
    python tools/install_deps.py --dry-run      # 只检测，不安装
    python tools/install_deps.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple
    python tools/install_deps.py --packages curl_cffi,requests   # 只处理指定包

退出码：全部就绪或安装成功=0；有安装失败=1（便于放进 CI / 定时任务告警）。
"""
import argparse
import glob
import importlib.util
import os
import re
import subprocess
import sys

# 模块名 -> pip 包名（只需列出"模块名与包名不一致或需要关注"的项）
PIP_NAMES = {
    "curl_cffi": "curl_cffi",
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "ddddocr": "ddddocr",
    "playwright": "playwright",
    "Crypto": "pycryptodome",
    "PIL": "pillow",
    "yaml": "pyyaml",
}

# 扫描源码里的 import（含函数内延迟 import，如 curl_cffi 那样）
IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))", re.M)


def is_stdlib(name):
    top = name.split(".")[0]
    if hasattr(sys, "stdlib_module_names"):
        return top in sys.stdlib_module_names
    return top in set(getattr(sys, "builtin_module_names", ()))  # 兜底


def scan_imports(script_dir):
    """扫描脚本，返回需要关注的第三方顶层模块名集合。"""
    found = set()
    for path in sorted(glob.glob(os.path.join(script_dir, "*.py"))):
        with open(path, encoding="utf-8", errors="ignore") as f:
            src = f.read()
        for m in IMPORT_RE.finditer(src):
            mod = (m.group(1) or m.group(2) or "").strip()
            if not mod:
                continue
            top = mod.split(".")[0]
            if not top or is_stdlib(top):
                continue
            found.add(top)
    return found


def installed(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def pip_install(pkg, mirror=None):
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", pkg]
    if mirror:
        cmd += ["-i", mirror]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, (r.stdout or b"").decode("utf-8", "ignore")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="Arcadia_Task 依赖自检/安装")
    ap.add_argument("--yes", "-y", action="store_true", help="不询问，直接安装缺失依赖")
    ap.add_argument("--dry-run", action="store_true", help="只检测不安装")
    ap.add_argument("--mirror", "-i", default=None, help="pip 镜像源，如 https://pypi.tuna.tsinghua.edu.cn/simple")
    ap.add_argument("--packages", default=None, help="只处理指定包（逗号分隔，Python 模块名）")
    ap.add_argument("--dir", default=os.path.join(root, "ZWZW"), help="扫描目录，默认 ZWZW/")
    args = ap.parse_args()

    if args.packages:
        mods = [m.strip() for m in args.packages.split(",") if m.strip()]
    else:
        mods = sorted(scan_imports(args.dir))

    if not mods:
        print("未发现第三方依赖（脚本均为标准库零依赖）。")
        return 0

    print("扫描目录: %s" % args.dir)
    print("依赖检测：")
    missing = []
    for m in mods:
        ok = installed(m)
        print("  - %-12s %s" % (m, "已安装" if ok else "缺失"))
        if not ok:
            missing.append(m)

    if not missing:
        print("\n全部依赖已就绪。")
        return 0

    pkgs = [PIP_NAMES.get(m, m) for m in missing]
    print("\n缺失依赖：%s" % ", ".join("%s(%s)" % (m, p) for m, p in zip(missing, pkgs)))

    if args.dry_run:
        print("（--dry-run，未执行安装）手动安装命令： pip install %s" % " ".join(pkgs))
        return 0

    if not args.yes:
        try:
            ans = input("是否现在安装？[Y/n] ").strip().lower()
        except EOFError:
            ans = "y"
        if ans and ans not in ("y", "yes", ""):
            print("已跳过。手动安装命令： pip install %s" % " ".join(pkgs))
            return 0

    failed = []
    for m, pkg in zip(missing, pkgs):
        print("安装 %s ..." % pkg)
        code, out = pip_install(pkg, args.mirror)
        if code != 0:
            failed.append(pkg)
            print("  失败：%s" % out.strip().splitlines()[-1][:200] if out.strip() else "  失败")
        else:
            print("  完成%s" % ("（镜像：%s）" % args.mirror if args.mirror else ""))

    if failed:
        print("\n以下依赖安装失败，请手动安装： pip install %s" % " ".join(failed))
        return 1
    print("\n全部依赖安装完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
