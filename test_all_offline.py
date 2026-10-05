# -*- coding: utf-8 -*-
"""test_all_offline.py — 一条命令跑完全部"离线安全"测试，并给出统一退出码。

为什么需要它（2026-10-05 审计结论）：
  1) 项目里 test_*.py 有 15 个，其中**5 个不能盲跑**，盲跑会出事故：
       - test_arduino_loop.py   会打开记事本、模拟按键、动你的剪贴板
       - test_key.py            会聚焦游戏窗口并按键（要游戏在跑）
       - test_view_lock.py      会截屏 + 按键转视角（要游戏在跑）
       - test_memory.py         要管理员权限、读游戏内存、写 memory_test_result.txt
       - test_chat_engine.py / test_deepseek_vision.py  真实调用付费 API
     所以"直接 py test_*.py"是不安全的，必须有一份显式的白名单。
  2) 各测试的结果约定不统一：有的 [PASS]/[FAIL] + sys.exit，有的裸 assert，
     有的只 print 结论（历史上 test_confirm_fix.py 失败也 exit 0）。
     本脚本用"退出码 + 输出标记 + PASS=n FAIL=n 解析"三重判据，避免误判。

用法：
    py test_all_offline.py          # 跑全部离线测试
    py test_all_offline.py --list   # 只列出会跑/会跳过哪些，不执行

退出码：0 = 全通过；1 = 有失败（含超时）。
注意：本文件不把自己列进 SAFE，避免自我递归。
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PER_TEST_TIMEOUT = 600      # 单个测试上限（test_ocr_enhance 跑真实 OCR，最慢）

# 离线安全：不碰游戏、不碰硬件、不碰剪贴板、不联网、不需要管理员
SAFE = [
    "test_confirm_fix.py",      # confirm 误判修复（panel_detector.ingest_ocr）
    "test_pose_fix.py",         # 姿势补按次数 / 是否需要补按
    "test_guard_reopen.py",     # 守门 reopen 纯函数
    "test_vision.py",           # vision_client 纯函数 + 好友树菜单判据
    "test_knowledge.py",        # 本地知识库 schema 校验 + 查询边界
    "test_pose_verify.py",      # 姿势校验：两次观察/补按距离/回退盲信（mock 视觉）
    "test_tool_loop.py",        # chat_raw 两条路径 + ai_loop 视觉工具循环 + ACT 解析
    "test_adversarial_tq.py",   # task_queue 对抗验证（重投总预算 / DROP 跨类）
    "test_logic.py",            # 识别→解析→去重→过滤 纯函数
    "test_task_queue.py",       # task_queue 完整单测
    "test_doctor.py",           # doctor.py 检查逻辑
    "test_ocr_enhance.py",      # OCR 增强（会跑真实 OCR，最慢，放最后）
]

# 不跑的，以及为什么 —— 留着给下一个接手的人看，别手滑
SKIP = [
    ("test_arduino_loop.py", "会打开记事本+模拟按键+改剪贴板，必须人工在场"),
    ("test_arduino_key.py", "需要 Arduino 硬件在 COM 口上"),
    ("test_key.py", "会往游戏窗口按键，需要游戏在跑"),
    ("test_view_lock.py", "截屏+按键转视角，需要游戏在跑"),
    ("test_memory.py", "需要管理员权限、读游戏内存、写结果文件"),
    ("test_chat_engine.py", "会真实调用 LLM（花钱）"),
    ("test_deepseek_vision.py", "会真实调用付费视觉 API"),
    ("test_yolo.py", "会开 cv2 窗口阻塞到按 q"),
]


def judge(name, code, out):
    """三重判据：退出码 + 失败标记 + PASS=n/FAIL=n 解析。返回 (是否通过, PASS数, 备注)。"""
    fail_reasons = []
    if code != 0:
        fail_reasons.append("exit=%d" % code)
    if "[FAIL]" in out:
        fail_reasons.append("有 [FAIL] 行")
    if "有失败" in out:
        fail_reasons.append("打印了'有失败'")
    if "Traceback" in out:
        fail_reasons.append("有异常栈")
    m = re.search(r"FAIL=(\d+)", out)
    if m and int(m.group(1)) > 0:
        fail_reasons.append("FAIL=%s" % m.group(1))

    mp = re.search(r"PASS=(\d+)", out)
    if mp:
        npass = int(mp.group(1))
    else:
        npass = out.count("[PASS]")
        if npass == 0:
            npass = len(re.findall(r"(?m)^\s*PASS[: ]", out))   # 裸 assert 型
    return (not fail_reasons), npass, "; ".join(fail_reasons)


def main(argv):
    if "--list" in argv:
        print("会跑的离线测试（%d 个）：" % len(SAFE))
        for t in SAFE:
            print("  [RUN ] %s" % t)
        print("\n会跳过的（%d 个）：" % len(SKIP))
        for t, why in SKIP:
            print("  [SKIP] %-26s %s" % (t, why))
        return 0

    print("=" * 96)
    print("光遇 AI 伙伴 · 离线测试总跑（%d 个；跳过 %d 个需要硬件/游戏/花钱的）"
          % (len(SAFE), len(SKIP)))
    print("=" * 96)
    print("%-26s %-6s %-6s %-6s %s" % ("脚本", "退出码", "通过项", "结果", "备注"))
    print("-" * 96)

    failed = []
    total_pass = 0
    for name in SAFE:
        if not os.path.exists(os.path.join(HERE, name)):
            print("%-26s %-6s %-6s %-6s %s" % (name, "-", "-", "缺失", "文件不存在"))
            failed.append(name)
            continue
        try:
            p = subprocess.run([sys.executable, name], cwd=HERE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=PER_TEST_TIMEOUT)
            out = p.stdout.decode("cp936", "replace")
            ok, npass, why = judge(name, p.returncode, out)
        except subprocess.TimeoutExpired:
            ok, npass, why, p = False, 0, "超时 >%ds" % PER_TEST_TIMEOUT, None
        total_pass += npass
        if not ok:
            failed.append(name)
        print("%-26s %-6s %-6s %-6s %s"
              % (name, "-" if p is None else p.returncode,
                 npass if npass else "-（不用 [PASS] 标记，以退出码为准）",
                 "通过" if ok else "未通过", why))

    print("-" * 96)
    print("共用例数（含各测试自报的 PASS）: %d" % total_pass)
    if failed:
        print("失败的测试: %s" % ", ".join(failed))
        print("RESULT: FAIL %d/%d" % (len(SAFE) - len(failed), len(SAFE)))
        return 1
    print("RESULT: ALL PASS %d/%d" % (len(SAFE), len(SAFE)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
