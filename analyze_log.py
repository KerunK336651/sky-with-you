# -*- coding: utf-8 -*-
"""
analyze_log.py — 启动日志快速体检（离线，不需要游戏/硬件）

挂机报错后不用翻长日志，运行本工具自动提取：运行环境、运行时长、正常/崩溃、
各类事件次数（OCR 读到消息 / AI 回复 / 白名单过滤 / MCP 重试 / YOLO 检测）、
以及所有未捕获异常的类型与位置。

用法：
  py analyze_log.py              分析 logs/ 里最新一次启动日志
  py analyze_log.py --all        列出 logs/ 里全部启动日志的总览
  py analyze_log.py --file X.txt 分析指定日志
  py analyze_log.py --dir 目录    指定日志目录
"""
import os
import re
import sys
import glob
import argparse
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))

# 事件统计规则：名称 -> 命中函数（行文本 -> bool）。标签与 sky-loop-v7.py 实际打印对齐。
def _is_ocr_msg(ln):
    return "[OCR] " in ln and not any(k in ln for k in ("线程启动", "线程退出", "异常", "玩家说"))

RULES = [
    ("OCR读到消息", lambda ln: _is_ocr_msg(ln)),
    ("AI回复", lambda ln: "[AI] ->" in ln),
    ("AI空转idle", lambda ln: "[AI] (idle" in ln),
    ("AI防重复改写", lambda ln: "Repeat: rewrite" in ln),
    ("对话压缩", lambda ln: "[AI] 压缩" in ln and "失败" not in ln),
    ("白名单过滤(次)", lambda ln: "[白名单] 过滤掉" in ln),
    ("MCP重试", lambda ln: "[MCP] 超时，重试" in ln),
    ("YOLO牵手", lambda ln: "检测到牵手图标" in ln),
    ("YOLO输入框", lambda ln: "检测到聊天输入框" in ln),
]
ERR_KEYS = ("Traceback", "未捕获异常", "Error", "Exception", "异常:", "失败", "错误")
EXIT_MARK = "===== 进程退出"
HEAD_TIME_RE = re.compile(r"启动日志\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
FNAME_TIME_RE = re.compile(r"loop_(\d{8}_\d{6})\.txt$")
EXIT_TIME_RE = re.compile(r"进程退出\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _fname_time(path):
    m = FNAME_TIME_RE.search(os.path.basename(path))
    return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") if m else None


def analyze(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    lines = text.splitlines()
    r = {"path": path, "counts": {k: 0 for k, _ in RULES}, "errors": [],
         "head": [], "clean_exit": any(EXIT_MARK in x for x in lines),
         "start": _fname_time(path), "end": None, "dur": None}
    # 启动头（两个 ==== 之间）
    sep = [i for i, x in enumerate(lines) if x.startswith("=" * 10)]
    if len(sep) >= 2:
        r["head"] = [x for x in lines[sep[0] + 1:sep[1]] if x.strip()]
    m = HEAD_TIME_RE.search(text)
    if m:
        r["head_time"] = m.group(1)
    me = EXIT_TIME_RE.search(text)
    if me:
        r["end"] = datetime.strptime(me.group(1), "%Y-%m-%d %H:%M:%S")
    if r["start"] and r["end"]:
        sec = int((r["end"] - r["start"]).total_seconds())
        r["dur"] = f"{sec//3600}时{(sec%3600)//60}分{sec%60}秒"

    for i, ln in enumerate(lines):
        for name, fn in RULES:
            try:
                if fn(ln):
                    r["counts"][name] += 1
            except Exception:
                pass
        if "!!! " in ln and "未捕获异常" in ln:
            tail = []
            for j in range(i + 1, min(i + 25, len(lines))):
                if lines[j].startswith("!!! ") or EXIT_MARK in lines[j]:
                    break
                tail.append(lines[j])
            # 取 traceback 最后一个像异常类型的行做摘要
            summ = next((x.strip() for x in reversed(tail)
                         if re.search(r"(Error|Exception|错误|异常)", x)), ln.strip(" !"))
            r["errors"].append((ln.strip(" !"), summ, tail))
        elif re.search(r"\[(OCR|AI|YOLO|MCP)\][^\n]*(异常|失败)", ln):
            r["errors"].append(("运行期错误", ln.strip(), []))
    return r


def print_report(r, show_tb=8):
    print("=" * 60)
    print("日志:", os.path.basename(r["path"]))
    if r.get("head_time"):
        print("启动时间:", r["head_time"], "| 运行时长:", r["dur"] or "未正常结束（可能崩溃或被关闭）")
    print("退出状态:", "正常退出(有进程退出标记)" if r["clean_exit"] else "未见正常退出标记")
    if r["head"]:
        print("-" * 60 + "\n运行环境:")
        for h in r["head"]:
            print("  " + h)
    print("-" * 60 + "\n事件统计:")
    for k, v in r["counts"].items():
        if v:
            print(f"  {k:14s}: {v}")
    nz = sum(r["counts"].values())
    if not nz:
        print("  （没有捕获到业务事件，可能启动后就没进入主循环）")
    print("-" * 60)
    if r["errors"]:
        print(f"异常/错误 {len(r['errors'])} 处:")
        for idx, (title, summ, tail) in enumerate(r["errors"], 1):
            print(f"  [{idx}] {title}")
            print(f"      -> {summ}")
            for t in tail[-show_tb:]:
                if t.strip():
                    print("       " + t)
    else:
        print("未捕获异常/运行期错误: 无")
    # 结论
    print("-" * 60 + "结论: ", end="")
    if r["errors"]:
        print("存在异常，重点看上面的 traceback；把本日志发给 AI 即可。")
    elif not r["clean_exit"]:
        print("无异常记录但没有正常退出标记，可能被手动关闭/强杀或窗口被关。")
    else:
        print("本次运行健康，无异常、正常退出。")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="指定日志文件")
    ap.add_argument("--dir", default=os.path.join(ROOT, "logs"))
    ap.add_argument("--all", action="store_true", help="总览全部日志")
    ap.add_argument("--tb", type=int, default=8, help="每个异常显示的traceback行数")
    args = ap.parse_args()

    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.dir, "loop_*.txt")),
                       key=os.path.getmtime, reverse=True)
    if not files:
        print("没找到日志。目录:", args.dir)
        print("先正常双击 start_loop.bat 跑一次，会在 logs/ 生成 loop_时间.txt；或用 --file 指定。")
        return
    if args.all and not args.file:
        print(f"{'日志文件':28s} {'启动':19s} {'时长':12s} {'异常':4s} {'AI回复':6s} {'OCR':5s} 状态")
        for p in files:
            r = analyze(p)
            print(f"{os.path.basename(p):28s} {str(r['start']):19s} {str(r['dur'] or '-'):12s} "
                  f"{len(r['errors']):<4d} {r['counts']['AI回复']:<6d} "
                  f"{r['counts']['OCR读到消息']:<5d} "
                  f"{'正常' if r['clean_exit'] and not r['errors'] else '需查看'}")
        print("\n要看最新一次详情，直接运行 py analyze_log.py")
        return
    print_report(analyze(files[0]), show_tb=args.tb)


if __name__ == "__main__":
    main()
