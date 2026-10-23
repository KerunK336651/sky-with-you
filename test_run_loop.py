# -*- coding: utf-8 -*-
"""test_run_loop.py — 看护进程 run_loop.py 的离线校验。

不跑游戏、不联网、不动真实记忆文件（用 _tmp/ 下的临时 state）。
其中"端到端"那组会真的起子进程，但子进程只是一个立刻退出的小片段，
只用来验证「崩溃退出码 -> 降级 CPU -> 再崩就放弃」这条链路。
"""
import json
import os
import subprocess
import sys

import run_loop as R

npass = 0
nfail = 0


def check(desc, ok):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    print("  [%s] %s" % ("PASS" if ok else "FAIL", desc))


TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")
os.makedirs(TMP, exist_ok=True)
STATE = os.path.join(TMP, "test_run_loop_state.json")
RUNLOG = os.path.join(TMP, "test_run_loop_child.log")

print("A. 退出码归一化")

check("as_unsigned(-1073741819) = 0xC0000005",
      R.as_unsigned(-1073741819) == 0xC0000005)
check("as_unsigned(3221225477) = 0xC0000005（同一码的正数形式）",
      R.as_unsigned(3221225477) == 0xC0000005)
check("as_signed(0xC0000005) = -1073741819",
      R.as_signed(0xC0000005) == -1073741819)
check("as_signed(0) = 0", R.as_signed(0) == 0)
check("as_signed(1) = 1", R.as_signed(1) == 1)
check("as_signed 对 0x80000000 取负数边界",
      R.as_signed(0x80000000) == -2147483648)

print()
print("B. is_native_crash（崩溃判据 —— 这是整个功能的地基）")

for code in (0xC0000005, 0xC0000409, 0xC0000374, 0xC00000FD, 0xC0000094, 0xC000001D):
    check("0x%08X 判为原生崩溃" % code,
          R.is_native_crash(code) and R.is_native_crash(-(0x100000000 - code)))
check("负数形式 -1073741819 也判为崩溃", R.is_native_crash(-1073741819))
check("0（正常退出）不是崩溃", R.is_native_crash(0) is False)
check("1（普通 Python 报错）不是崩溃", R.is_native_crash(1) is False)
check("2 不是崩溃", R.is_native_crash(2) is False)
check("None 不是崩溃", R.is_native_crash(None) is False)
check("0xBFFFFFFF 不在区间内（低位边界外）", R.is_native_crash(0xBFFFFFFF) is False)
check("0xD0000000 不在区间内（高位边界外）", R.is_native_crash(0xD0000000) is False)

print()
print("C. describe_rc（打印用的人话）")

check("0 -> 正常退出", "正常退出" in R.describe_rc(0))
check("崩溃码带出 0xC0000005", "0xC0000005" in R.describe_rc(-1073741819))
check("崩溃码带上「访问违例」字样", "访问违例" in R.describe_rc(-1073741819))
check("崩溃码带上有符号值 -1073741819", "-1073741819" in R.describe_rc(-1073741819))
check("rc=1 明确写「不是原生崩溃」", "不是原生崩溃" in R.describe_rc(1))
check("未收录的崩溃码也不崩", "未收录" in R.describe_rc(0xC0001234))

print()
print("D. decide（决策表）")

# 正常退出 -> 退出，不重启
a, d, why = R.decide(0, "auto", restarts_left=1)
check("正常退出 -> exit 且设备不变", a == "exit" and d == "auto")
# 普通报错 -> 退出，**绝不能重启**（否则变成无限重启循环）
a, d, why = R.decide(1, "auto", restarts_left=1)
check("普通报错 rc=1 -> exit（不重启，防止无限循环）", a == "exit" and d == "auto")
a, d, why = R.decide(2, "dml", restarts_left=5)
check("普通报错 rc=2 有重启额度也不重启", a == "exit")
# 崩溃 -> 降级 CPU
a, d, why = R.decide(-1073741819, "auto", restarts_left=1)
check("auto 崩溃 -> restart_cpu", a == "restart_cpu" and d == "cpu")
a, d, why = R.decide(-1073741819, "dml", restarts_left=1)
check("dml 崩溃 -> restart_cpu", a == "restart_cpu" and d == "cpu")
check("降级原因里说明是原生崩溃", "原生崩溃" in why)
# CPU 也崩 -> 放弃（别再重启，纯属无用）
a, d, why = R.decide(-1073741819, "cpu", restarts_left=5)
check("cpu 崩溃 -> give_up（不再重启）", a == "give_up" and d == "cpu")
check("give_up 原因说明不是 OCR 设备的问题", "不是 OCR 设备" in why)
# 次数用完
a, d, why = R.decide(-1073741819, "auto", restarts_left=0)
check("额度用完 -> give_up", a == "give_up")
# --no-restart
a, d, why = R.decide(-1073741819, "auto", restarts_left=3, no_restart=True)
check("--no-restart 时崩溃 -> give_up", a == "give_up")
# Ctrl+C
a, d, why = R.decide(None, "auto", restarts_left=1, interrupted=True)
check("Ctrl+C -> exit（不当成崩溃）", a == "exit")
a, d, why = R.decide(-1073741819, "auto", restarts_left=1, interrupted=True)
check("Ctrl+C 优先于崩溃码 -> exit", a == "exit")

print()
print("E. 记忆文件读写")

if os.path.exists(STATE):
    os.remove(STATE)
check("文件不存在 -> 空 dict（不抛异常）", R.load_memory(STATE) == {})
mem = {"dml_crashed": True, "device": "auto", "crash": "0xC0000005", "count": 1}
check("写入成功", R.save_memory(STATE, mem) is True)
back = R.load_memory(STATE)
check("读回来内容一致", back == mem)
check("落盘的是合法 JSON", isinstance(json.load(open(STATE, encoding="utf-8")), dict))
# 坏文件
with open(STATE, "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
check("坏文件 -> 空 dict（绝不因记忆文件启动失败）", R.load_memory(STATE) == {})
check("坏文件时 save 仍能覆盖成功", R.save_memory(STATE, mem) is True)
check("覆盖后可正常读回", R.load_memory(STATE) == mem)

print()
print("F. 端到端：真的起子进程，验证「崩溃 -> 降级 CPU -> 再崩放弃」")

# 假子进程：把 SKY_OCR_DEVICE 追加到日志，然后按 FAKE_RC 退出
FAKE = ("import os,sys\n"
        "open(os.environ['FAKE_LOG'],'a',encoding='utf-8').write("
        "os.environ.get('SKY_OCR_DEVICE','?')+'\\n')\n"
        "sys.exit(int(os.environ.get('FAKE_RC','-1073741819')))\n")
FAKE_CMD = [sys.executable, "-c", FAKE]
os.environ["FAKE_LOG"] = RUNLOG


def child_runs():
    if not os.path.exists(RUNLOG):
        return []
    return [l.strip() for l in open(RUNLOG, encoding="utf-8").read().split("\n") if l.strip()]


# --- 场景 1：崩溃 -> 降级 CPU 重启 -> CPU 又崩 -> 放弃（共 2 次）---
os.environ["FAKE_RC"] = "-1073741819"
if os.path.exists(RUNLOG):
    os.remove(RUNLOG)
if os.path.exists(STATE):
    os.remove(STATE)
rc = R.main(["--device", "auto", "--delay", "0", "--state", STATE], cmd=FAKE_CMD)
runs = child_runs()
check("崩溃场景：子进程正好跑了 2 次（auto 一次、cpu 一次）", len(runs) == 2)
check("第一次用 auto", runs[:1] == ["auto"])
check("第二次降级成 cpu", runs[1:] == ["cpu"])
check("退出码沿用最后一次崩溃码", rc == -1073741819)
mem = R.load_memory(STATE)
check("已记住 DML 崩过", mem.get("dml_crashed") is True)
check("记忆里记了设备与时间", mem.get("device") == "auto" and bool(mem.get("when")))
check("记忆里记了崩溃描述", "0xC0000005" in str(mem.get("crash", "")))

# --- 场景 2：有记忆时，下次启动直接上 CPU（不再先崩一次）---
if os.path.exists(RUNLOG):
    os.remove(RUNLOG)
rc = R.main(["--delay", "0", "--state", STATE], cmd=FAKE_CMD)
runs = child_runs()
check("有记忆时首次就用 cpu（不再先崩一次）", runs[:1] == ["cpu"])
check("cpu 崩了就放弃，总次数 1", len(runs) == 1)

# --- 场景 3：显式 --device auto 时，无视记忆照你说的试 ---
if os.path.exists(RUNLOG):
    os.remove(RUNLOG)
R.main(["--device", "auto", "--delay", "0", "--state", STATE], cmd=FAKE_CMD)
runs = child_runs()
check("显式 --device auto 时首次是 auto（记忆不拦）", runs[:1] == ["auto"])

# --- 场景 4：正常退出 -> 只跑 1 次，不重启 ---
os.environ["FAKE_RC"] = "0"
if os.path.exists(RUNLOG):
    os.remove(RUNLOG)
rc = R.main(["--device", "auto", "--delay", "0", "--state",
             os.path.join(TMP, "test_run_loop_state2.json")], cmd=FAKE_CMD)
check("正常退出：只跑 1 次", len(child_runs()) == 1)
check("正常退出：返回 0", rc == 0)

# --- 场景 5：普通报错 -> 只跑 1 次（不进入重启循环）---
os.environ["FAKE_RC"] = "1"
if os.path.exists(RUNLOG):
    os.remove(RUNLOG)
rc = R.main(["--device", "auto", "--delay", "0", "--state",
             os.path.join(TMP, "test_run_loop_state2.json")], cmd=FAKE_CMD)
check("普通报错：只跑 1 次（不会无限重启）", len(child_runs()) == 1)
check("普通报错：退出码 1", rc == 1)

# --- 场景 6：--reset 清记忆 ---
R.save_memory(STATE, {"dml_crashed": True, "count": 3})
rc = R.main(["--reset", "--state", STATE])
check("--reset 返回 0", rc == 0)
check("--reset 后记忆为空", R.load_memory(STATE) == {})

# --- 场景 7：--no-restart ---
os.environ["FAKE_RC"] = "-1073741819"
if os.path.exists(RUNLOG):
    os.remove(RUNLOG)
os.remove(STATE) if os.path.exists(STATE) else None
rc = R.main(["--device", "auto", "--no-restart", "--delay", "0", "--state", STATE], cmd=FAKE_CMD)
check("--no-restart：崩溃也只跑 1 次", len(child_runs()) == 1)

print()
print("G. 真实崩溃码确实来自 Windows（用真子进程验证 sys.exit 的码能对上）")

p = subprocess.run([sys.executable, "-c", "import sys; sys.exit(-1073741819)"])
check("子进程 sys.exit(-1073741819) 的 returncode 判为原生崩溃",
      R.is_native_crash(p.returncode))
check("同一个码的无符号形式确实是 0xC0000005",
      R.as_unsigned(p.returncode) == 0xC0000005)

print()
print("H. 源码 GBK 安全")
try:
    open(R.__file__, "rb").read().decode("utf-8").encode("gbk")
    gbk_ok = True
except UnicodeEncodeError as e:
    gbk_ok = False
    print("      编不出的字符：%s" % e)
check("run_loop.py 可被 GBK 编码", gbk_ok)

# 清理
for p in (STATE, RUNLOG, os.path.join(TMP, "test_run_loop_state2.json")):
    try:
        os.remove(p)
    except Exception:
        pass

print()
print("PASS=%d FAIL=%d" % (npass, nfail))
if nfail:
    print("有 %d 条失败" % nfail)
    sys.exit(1)
print("全部通过")
