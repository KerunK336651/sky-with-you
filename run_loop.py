# -*- coding: utf-8 -*-
"""看护进程：跑 sky-loop-v7.py，**原生崩溃时自动降级到 CPU OCR 并重启**。

为什么必须是外部看护进程
------------------------
2026-10-06 真机崩的是 `0xC0000005` 访问违例，出错模块 `amdxc64.dll`（AMD 显卡驱动的
DirectX 组件），调用链是 ORT -> DirectML -> D3D12Core -> amdxc64。
这是**原生层崩溃，进程当场消失**：`try/except` 抓不到，`atexit` 不执行，
`signal` 收不到（除了已经没了的进程），`faulthandler` 只能把栈打到 stderr 也救不回来。
所以"崩了自动切 CPU"只能靠父进程看子进程的**退出码**来判断，再换个配置重开一个进程。

判定规则（is_native_crash）
--------------------------
Windows 上被异常终止的进程，退出码落在 `0xC0000000` ~ `0xCFFFFFFF`：
    0xC0000005 访问违例 / 0xC0000409 fail-fast / 0xC0000374 堆损坏 / 0xC00000FD 栈溢出 …
而普通 Python 异常退出是 1、正常退出是 0、Ctrl+C 一般是 0 或 1 —— **都不算崩溃**，
看护进程不会重启、也不会降级（否则会把你自己的报错变成一个无限重启循环）。

用法
----
    py run_loop.py                  # 按 SKY_OCR_DEVICE（bat 里设的）启动；崩了自动降级 CPU 并重启一次
    py run_loop.py --device cpu     # 直接用 CPU
    py run_loop.py --device auto    # 忽略"上次崩过"的记忆，再用 DML 试一次
    py run_loop.py --reset          # 清除记忆（比如换了显卡驱动想重试 DML）
    py run_loop.py --no-restart     # 不自动重启（调试用，崩了就退）

记忆文件：`user_data/loop_supervisor.json`。DML 崩过一次就记住，下次启动直接用 CPU，
免得每次都要先崩一遍。想重试 DML 用 `--device auto` 或先 `--reset`。
"""
import argparse
import ctypes
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "sky-loop-v7.py")
STATE = os.path.join(HERE, "user_data", "loop_supervisor.json")

# Windows 异常终止的退出码区间
CRASH_LO, CRASH_HI = 0xC0000000, 0xCFFFFFFF

# 几个常见崩溃码的含义（打印用）
CRASH_NAMES = {
    0xC0000005: "访问违例（ACCESS_VIOLATION）",
    0xC0000094: "整数除零",
    0xC000001D: "非法指令",
    0xC00000FD: "栈溢出",
    0xC0000374: "堆损坏",
    0xC0000409: "fail-fast / 栈缓冲区越界",
}


# ───────────────────────── 纯逻辑（可离线测试） ─────────────────────────

def as_unsigned(rc):
    """把 Windows 退出码统一成无符号 32 位（Popen 可能给负数，也可能给大正数）。"""
    return int(rc) & 0xFFFFFFFF


def as_signed(rc):
    """统一成有符号 32 位，便于打印（0xC0000005 -> -1073741819）。"""
    u = as_unsigned(rc)
    return u - 0x100000000 if u >= 0x80000000 else u


def is_native_crash(rc):
    """退出码是否表示原生崩溃（被异常终止），而不是正常退出/普通 Python 报错。"""
    if rc is None:
        return False
    return CRASH_LO <= as_unsigned(rc) <= CRASH_HI


def describe_rc(rc):
    """给退出码一句人话。"""
    if rc is None:
        return "（没有退出码）"
    if rc == 0:
        return "正常退出"
    if is_native_crash(rc):
        u = as_unsigned(rc)
        return "原生崩溃 0x%08X %s（有符号 %d）" % (u, CRASH_NAMES.get(u, "未收录"), as_signed(rc))
    return "退出码 %d（普通退出/报错，不是原生崩溃）" % as_signed(rc)


def decide(rc, device, restarts_left, interrupted=False, no_restart=False):
    """纯函数：根据子进程退出码决定下一步。

    返回 (action, next_device, reason)
      action: 'exit'        原样退出（正常退出 / 普通报错 / Ctrl+C / 关了自动重启）
              'restart_cpu' 用 CPU 重启
              'give_up'     崩了但已无计可施（CPU 也崩 / 没重启次数了）
    """
    if interrupted:
        return ("exit", device, "收到 Ctrl+C，不重启")
    if not is_native_crash(rc):
        return ("exit", device, "不是原生崩溃")
    if device == "cpu":
        return ("give_up", "cpu", "CPU 模式下仍然崩溃 —— 不是 OCR 设备的问题，"
                                  "请把最新的 dump 发给 DSH")
    if no_restart:
        return ("give_up", device, "指定了 --no-restart")
    if restarts_left <= 0:
        return ("give_up", device, "自动重启次数已用完")
    return ("restart_cpu", "cpu", "检测到原生崩溃，降级到 CPU OCR 后重启")


def load_memory(path=STATE):
    """读记忆文件；坏了/不存在都返回空 dict（绝不因为记忆文件而启动失败）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def save_memory(path, mem):
    """写记忆文件；失败只警告，不影响运行。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print("  [看护] 记忆文件写入失败（不影响运行）: %s" % e)
        return False


# ───────────────────────── 子进程 ─────────────────────────

def run_child(device, cmd=None, cwd=HERE):
    """用指定 OCR 设备跑一次主循环。返回 (退出码, 是否被 Ctrl+C 打断)。"""
    argv = list(cmd) if cmd else [sys.executable, LOOP]
    env = dict(os.environ)
    env["SKY_OCR_DEVICE"] = device
    print("  [看护] 启动: %s" % " ".join(argv))
    print("  [看护] SKY_OCR_DEVICE=%s" % device)
    sys.stdout.flush()
    proc = subprocess.Popen(argv, env=env, cwd=cwd)
    try:
        return proc.wait(), False
    except KeyboardInterrupt:
        print("\n  [看护] 收到 Ctrl+C，等子进程自己收尾…")
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        return proc.returncode, True


def main(argv=None, cmd=None):
    ap = argparse.ArgumentParser(description="看护 sky-loop-v7.py：原生崩溃时自动降级到 CPU")
    ap.add_argument("--device", choices=("auto", "dml", "cpu"), default=None,
                    help="OCR 推理设备；不给则用环境变量 SKY_OCR_DEVICE（默认 auto）")
    ap.add_argument("--max-restarts", type=int, default=1, help="崩溃后最多重启几次（默认 1）")
    ap.add_argument("--delay", type=float, default=3.0, help="重启前等待秒数（默认 3，给串口/驱动缓一下）")
    ap.add_argument("--no-restart", action="store_true", help="不自动重启，崩了就退")
    ap.add_argument("--reset", action="store_true", help="清除「DML 崩过」的记忆后退出")
    ap.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.reset:
        mem = load_memory(args.state)
        if mem:
            save_memory(args.state, {})
            print("已清除记忆：%s（下次会重新尝试 SKY_OCR_DEVICE 指定的设备）" % args.state)
        else:
            print("本来就没有记忆，无需清除")
        return 0

    mem = load_memory(args.state)
    device = args.device or (os.environ.get("SKY_OCR_DEVICE", "auto").strip().lower() or "auto")

    print("=" * 60)
    print("  光遇循环看护进程")
    print("=" * 60)
    if mem.get("dml_crashed"):
        print("  [记忆] 上次在 %s 用 %s 崩过：%s"
              % (mem.get("when", "?"), mem.get("device", "?"), mem.get("crash", "?")))
        if args.device is None and device != "cpu":
            print("  [记忆] 本次直接用 CPU（想重试 DML：py run_loop.py --device auto，"
                  "或先 py run_loop.py --reset）")
            device = "cpu"
        elif device != "cpu":
            print("  [记忆] 但你显式指定了 --device %s，照你说的试" % device)
    print("  OCR 设备: %s" % device)
    print("  自动重启: %s（最多 %d 次）"
          % ("关" if args.no_restart else "开", args.max_restarts))
    print("=" * 60)

    restarts_left = args.max_restarts
    while True:
        rc, interrupted = run_child(device, cmd=cmd)
        print("\n  [看护] 子进程结束：%s" % describe_rc(rc))
        action, next_device, why = decide(rc, device, restarts_left,
                                          interrupted=interrupted,
                                          no_restart=args.no_restart)
        if action == "exit":
            if why:
                print("  [看护] %s" % why)
            return as_signed(rc) if rc is not None else 0

        if action == "restart_cpu":
            print("  [看护] %s" % why)
            mem = load_memory(args.state)
            mem["dml_crashed"] = True
            mem["device"] = device
            mem["crash"] = describe_rc(rc)
            mem["when"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mem["count"] = int(mem.get("count", 0)) + 1
            save_memory(args.state, mem)
            print("  [看护] 已记住（下次启动直接用 CPU）。想重试 DML：py run_loop.py --reset")
            restarts_left -= 1
            device = next_device
            if args.delay > 0:
                print("  [看护] 等 %.1f 秒再重启（让串口/显卡驱动缓一下）…" % args.delay)
                import time
                time.sleep(args.delay)
            print()
            continue

        # give_up
        print("  [看护] %s" % why)
        print("  [看护] 不再自动重启。崩溃转储在 %LOCALAPPDATA%\\CrashDumps\\python.exe.*.dmp，"
              "可交给我用 _dump_analyze.py 解析。")
        return as_signed(rc) if rc is not None else 0


if __name__ == "__main__":
    sys.exit(main())
