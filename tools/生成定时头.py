#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成定时头.py —— 为 ZWZW/ 下的 Python 脚本维护 Arcadia 定时任务元数据注释
=================================================================================

使用方式（最常用）
----------------
在本仓库根目录执行下面一行即可：

    python tools/生成定时头.py

它会：
  - 读取 arcadia/tasks.json 的定时，写进每个脚本头部注释块（[Arcadia-AutoCron]）；
  - 重算脚本正文指纹并刷新 arcadia/tasks.state.json；
  - 加 --dry-run 只预览、不写任何文件。
完整参数与机制见下方各节。

背景
----
Arcadia（SuperManito/Arcadia，代码自动化运维平台）在执行 `arcadia update repo` /
`arcadia update raw` 时，会对"新出现的"代码文件做一次解析：从文件内容中检索**第一个**
合法的 5 段 / 6 段 Cron 表达式，命中则按它自动创建一条"系统任务"（执行命令为
`arcadia run <runPath>`）；若文件里没有合法表达式，则随机生成一个"每天执行一次"的
规则。因此只要在脚本文件头部写一行注释 `# cron: <表达式>`，导入时 Arcadia 就会
自动生成对应 cron 的定时任务。

本工具为每个脚本在 shebang 之后插入/刷新如下形式的注释块：

    # [Arcadia-AutoCron-BEGIN]
    # 更新时间: 2026-09-07
    # cron: 5 7 * * *
    # [Arcadia-AutoCron-END]

特性：
  1. 注释块一定是整个文件里被 Arcadia 扫描到的第一条 cron 候选；
  2. 重复执行幂等，不会产生重复块；
  3. 保留每个文件原有换行符（LF / CRLF）。

"更新时间"的维护规则（结合 arcadia/tasks.state.json 的指纹记录）：
  - 脚本首次加入：写入运行当天的日期；
  - 之后每次运行若发现以下任一变化，自动把时间刷新为当天：
      ① 该脚本的 cron（arcadia/tasks.json）变了；
      ② 脚本正文（去掉本注释块后的内容）变了；
      ③ 旧注释块还没有"更新时间"行（历史格式升级）。
  - 没有任何变化则保留原日期，文件也不会被重写。
这样"脚本上次改到哪一天"一眼可见，适合新增站点/调时间后统一刷新再推送到 Arcadia。

定时来源（优先级从高到低）
--------------------------
  1. arcadia/tasks.json 中 <文件名>: "<cron>" 的显式配置（推荐，交给仓库维护）；
  2. 未配置的脚本使用"由文件名稳定派生"的时刻（每天一次，位置固定可复现），
     且**只落在时间窗内**（默认 7-21 点，用 --slot-range 调整），避免派生到凌晨。

用法
----
  python tools/生成定时头.py                        # 刷新全部脚本并打印结果
  python tools/生成定时头.py --dry-run               # 只预览，不写任何文件
  python tools/生成定时头.py --config arcadia/tasks.json   # 指定定时配置
  python tools/生成定时头.py --state arcadia/tasks.state.json  # 指定状态文件
  python tools/生成定时头.py --dir ZWZW              # 指定脚本目录
  python tools/生成定时头.py --slot-range 7-21       # 未配置脚本的派生时间窗（默认 7-21，支持跨天 22-6）

与 Arcadia 配合的完整流程见仓库根目录 README.md「导入到 Arcadia」。
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date

# ---------------------------------------------------------------- 路径
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
DEFAULT_DIR = os.path.join(REPO_ROOT, "ZWZW")
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "arcadia", "tasks.json")
DEFAULT_STATE = os.path.join(REPO_ROOT, "arcadia", "tasks.state.json")

# ---------------------------------------------------------------- 标记
BEGIN = "# [Arcadia-AutoCron-BEGIN]"
END = "# [Arcadia-AutoCron-END]"
CRON_FIELD = "# cron: {cron}"
UPDATED_FIELD = "# 更新时间: {date}"
SHEBANG_RE = re.compile(r"^#!")
CRON_LINE_RE = re.compile(r"^#\s*cron\s*[:：]\s*(.+?)\s*$")
UPDATED_LINE_RE = re.compile(r"^#\s*更新时间\s*[:：]\s*(\d{4}-\d{2}-\d{2})")

# 与 Arcadia resolve 脚本同一字符集的 cron 候选识别（仅供自检）
CRON_CANDIDATE_RE = re.compile(
    r"(?:[0-9*/,\-]+\s+){4}[0-9*/,\-]+(?:\s+[0-9*/,\-]+)?"
)


# ---------------------------------------------------------------- cron 校验
def _check_token(tok, max_value):
    """近似 Arcadia resolve.sh 的字段校验：支持 * 、数值、a-b、a-b/c、*/c、逗号列表。"""
    for item in tok.split(","):
        item = item.strip()
        if not item:
            return False
        if "/" in item:
            base, _, step = item.partition("/")
            if not step.isdigit():
                return False
        else:
            base, step = item, None
        if base == "*":
            continue
        if "-" in base:
            lo_s, _, hi_s = base.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                return False
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi or hi > max_value:
                return False
        elif base.isdigit():
            if int(base) > max_value:
                return False
        else:
            return False
    return True


def validate_cron(expr):
    """5 段 / 6 段标准 Cron 校验；第 1 段为 6 段式时的秒字段。"""
    if not expr or not isinstance(expr, str):
        return False
    parts = expr.split()
    if len(parts) not in (5, 6):
        return False
    if len(parts) == 6:
        if not _check_token(parts[0], 59):  # 秒
            return False
        parts = parts[1:]
    max_values = [59, 23, 31, 12, 7]  # 分 时 日 月 周
    for i, tok in enumerate(parts):
        if not _check_token(tok, max_values[i]):
            return False
    return True


# ---------------------------------------------------------------- 默认时刻
SLOT_STEP = 5                  # 排班粒度（分钟）
DEFAULT_SLOT_RANGE = (7, 21)   # 未配置脚本的派生时间窗（小时，闭区间；按运行环境时区理解）


def parse_slot_range(spec, default=DEFAULT_SLOT_RANGE):
    """解析时间窗："7-21" / "7~21" / "22-6"（跨天）；非法则回退默认。"""
    s = (spec or "").strip().replace("~", "-")
    m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})$", s)
    if not m:
        return default
    a, b = int(m.group(1)), int(m.group(2))
    if not (0 <= a <= 23 and 0 <= b <= 23):
        return default
    return (a, b)


def slots_in_range(start, end, step=SLOT_STEP):
    """窗口内的 (hour, minute) 序列；start>end 表示跨天（如 22-6），start==end 视为全天。"""
    span = ((end - start) % 24) or 24
    n = max(1, span * 60 // step)
    slots = []
    h, m = start, 0
    for _ in range(n):
        slots.append((h, m))
        m += step
        if m >= 60:
            m = 0
            h = (h + 1) % 24
    return slots


def default_cron(file_name, slot_range=DEFAULT_SLOT_RANGE):
    """未配置 cron 的脚本：在给定时间窗内按文件名稳定派生一个时刻（每天一次、5 段）。

    同一个文件名 + 同一个窗口 → 结果固定可复现；默认窗口 7-21 点，避免出现凌晨执行。
    """
    seed = "%s|%d-%d|%d" % (file_name, slot_range[0], slot_range[1], SLOT_STEP)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    slots = slots_in_range(slot_range[0], slot_range[1])
    hour, minute = slots[(digest[0] << 8 | digest[1]) % len(slots)]
    return "%d %d * * *" % (minute, hour)


# ---------------------------------------------------------------- 注释块
def make_block(cron, updated_at):
    return [
        BEGIN,
        UPDATED_FIELD.format(date=updated_at),
        CRON_FIELD.format(cron=cron),
        END,
    ]


def split_block(lines):
    """把已有注释块从行列表里剥离。

    返回 (body, cron, updated_at, has_block)：
      body      去掉注释块后剩下的行；
      cron      块内解析出的 cron（没有则为 None）；
      updated_at 块内解析出的 YYYY-MM-DD（没有则为 None）；
      has_block 文件中是否存在本工具管理的注释块。
    """
    body, cron, updated_at = [], None, None
    has_block = False
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip() == BEGIN:
            has_block = True
            j = i + 1
            while j < n and lines[j].strip() != END:
                j += 1
            inner = lines[i + 1:j]
            for ln in inner:
                m = CRON_LINE_RE.match(ln)
                if m and cron is None:
                    cron = m.group(1).strip()
                m = UPDATED_LINE_RE.match(ln)
                if m and updated_at is None:
                    updated_at = m.group(1)
            i = j + 1 if j < n else n
            continue
        body.append(lines[i])
        i += 1
    return body, cron, updated_at, has_block


def body_fingerprint(body):
    """对"去掉本工具注释块"后的正文计算指纹，用于判断脚本是否被改动过。"""
    return hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()


def insert_after_shebang(body, block):
    """把注释块插入 shebang 之后（无 shebang 则放文件最前）。"""
    pos = 0
    for idx, line in enumerate(body):
        if SHEBANG_RE.match(line.lstrip()):
            pos = idx + 1
            break
    return body[:pos] + block + body[pos:]


# ---------------------------------------------------------------- 状态文件
def load_state(state_path):
    if state_path and os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def dump_state(state_path, state):
    """写状态文件（JSON），仅当内容确实变化时落盘。"""
    body = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    try:
        with open(state_path, "r", encoding="utf-8") as fp:
            old = fp.read()
    except OSError:
        old = None
    if old == body:
        return False
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    with open(state_path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(body)
    return True


# ---------------------------------------------------------------- 文件读写
def read_text(path):
    """读取 UTF-8 文本；返回 (text, newline)。"""
    raw = open(path, "rb").read()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    bom = raw.startswith(b"\xef\xbb\xbf")
    data = raw[3:] if bom else raw
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("gbk")  # 兜底
    return text, newline


def first_cron_candidate(text):
    """自检：找到文件中被 Arcadia 当作首个 cron 候选的行号与内容。"""
    for idx, line in enumerate(text.splitlines(), 1):
        m = CRON_CANDIDATE_RE.search(line)
        if m:
            return idx, m.group(0).strip()
    return None, None


def collect_py_files(script_dir):
    if not os.path.isdir(script_dir):
        print("错误：脚本目录不存在：%s" % script_dir, file=sys.stderr)
        sys.exit(2)
    return sorted(
        f for f in os.listdir(script_dir)
        if f.endswith(".py") and os.path.isfile(os.path.join(script_dir, f))
    )


def load_config(config_path):
    cfg = {}
    if config_path and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str) and k.endswith(".py"):
                    cfg[k] = v
        print("已读取定时配置：%s（%d 条）" % (config_path, len(cfg)))
    elif config_path:
        print("提示：未找到 %s，将使用默认派生时刻。" % config_path)
    return cfg


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="维护 ZWZW 脚本头部的 Arcadia cron / 更新时间注释块")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="脚本所在目录（默认 ZWZW/）")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="定时配置 JSON 路径")
    ap.add_argument("--state", default=DEFAULT_STATE, help="更新时间状态 JSON 路径")
    ap.add_argument("--slot-range", default="7-21",
                    help="未配置脚本的派生时间窗（小时，默认 7-21，支持跨天如 22-6；按运行环境时区理解）")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = ap.parse_args()

    slot_range = parse_slot_range(args.slot_range)
    config = load_config(args.config)
    prev_state = load_state(args.state)
    files = collect_py_files(args.dir)
    if not files:
        print("该目录下没有 .py 脚本：%s" % args.dir, file=sys.stderr)
        sys.exit(2)

    today = date.today().isoformat()

    # 第一遍：读取 + 校验 + 计算，全部通过才进入写盘，避免部分生效
    invalid_any = False
    table = []          # (path, name, cron, updated, status, line_no, newline, new_lines, write)
    for name in files:
        path = os.path.join(args.dir, name)
        cron = config.get(name) or default_cron(name, slot_range)
        if not validate_cron(cron):
            print("错误：%s 的 cron 表达式不合法：%r" % (name, cron), file=sys.stderr)
            invalid_any = True
            continue

        text, newline = read_text(path)
        cur_lines = text.splitlines()
        body, old_cron, old_updated, has_block = split_block(cur_lines)
        fingerprint = body_fingerprint(body)
        prev = prev_state.get(name)

        # ---- 判定是否需要刷新"更新时间"，并给出状态 ----
        if not has_block:
            # 该脚本首次纳入管理：新增
            updated_at, status, write = today, "新增", True
        else:
            cron_changed = old_cron != cron
            updated_missing = old_updated is None          # 历史格式，尚无时间行
            content_diff = bool(prev) and prev.get("body") != fingerprint
            if cron_changed:
                updated_at, status, write = today, "更新(cron)", True
            elif updated_missing:
                updated_at, status, write = today, "更新时间", True
            elif content_diff:
                updated_at, status, write = today, "更新", True
            else:
                updated_at, status, write = old_updated, "不变", False

        block = make_block(cron, updated_at)
        new_lines = insert_after_shebang(body, block)
        # 自检：头部块必须是文件第一条 cron 候选；若出现更早的干扰序列则报错，避免误配
        line_no, candidate = first_cron_candidate("\n".join(new_lines))
        if candidate != cron:
            print("错误：%s 第 %s 行存在更早的 cron 候选：%r，请人工检查该文件头部"
                  % (name, line_no, candidate), file=sys.stderr)
            invalid_any = True
            continue

        table.append((path, name, cron, updated_at, status, line_no, newline, new_lines, write))
        prev_state[name] = {"cron": cron, "body": fingerprint, "updated": updated_at}

    if invalid_any:
        print("\n存在未通过校验的脚本，请修复后重试（本次未写入任何文件）。")
        sys.exit(1)

    # 状态文件只保留当前存在的脚本，避免残留陈旧记录
    prev_state = {k: v for k, v in prev_state.items() if k in files}

    print("\n%-22s %-14s %-12s %-10s %s" % ("脚本文件", "cron（每天）", "更新时间", "状态", "cron行"))
    print("-" * 100)
    for _path, name, cron, updated_at, status, line_no, _nl, _new, _write in table:
        print("%-22s %-14s %-12s %-10s %s" % (name, cron, updated_at or "-", status, line_no))

    write_any = sum(1 for _p, _n, _c, _u, s, _l, _nl, _w, wr in table if wr and s != "新增")
    add_any = sum(1 for _p, _n, _c, _u, s, _l, _nl, _w, wr in table if wr and s == "新增")

    if args.dry_run:
        print("\n[dry-run] 未写入任何文件%s。" % ("（有待写入的变更）" if write_any + add_any else "，全部已是最新"))
        return 0

    # 第二遍：统一写盘（先脚本，后状态文件）
    written = 0
    for _path, _name, _cron, _updated, _status, _line_no, newline, new_lines, write in table:
        if not write:
            continue
        content = "\n".join(new_lines)
        if content:
            content += "\n"
        with open(_path, "w", encoding="utf-8", newline=newline) as fp:
            fp.write(content)
        written += 1
    state_dirty = dump_state(args.state, prev_state)

    if written or state_dirty:
        print("\n完成：更新 %d 个脚本（新增 %d / 刷新 %d），状态文件%s。"
              % (written, add_any, write_any, "已更新" if state_dirty else "无变化"))
    else:
        print("\n完成：所有脚本均已一致，无变更。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
