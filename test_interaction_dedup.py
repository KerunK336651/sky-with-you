# -*- coding: utf-8 -*-
"""
test_interaction_dedup.py — 近期互动记忆 + 防重复发起（离线，不需游戏/硬件）

对应 loop_20261025_011549 日志里的"重复抱抱"：玩家发起抱抱被接受后，OCR 延迟读到
"抱一下嘛"，AI 又主动发起一次抱抱。修复 = 记录近期互动 + 注入上下文 + 45s 同动作软兜底。

验证：
  1. 接受/发起互动后会进 prompt，且写清"谁发起、多久前"；
  2. 同动作刚做完、无新邀请 -> is_duplicate_initiate 为真（跳过重复发起）；
  3. 屏幕上又有新邀请 / 窗口已过 / 不同动作 / 兜底关闭 -> 不拦；
  4. 超出记忆窗口的事件被清理；同动作只显示最近一次。

用法:  py test_interaction_dedup.py
"""
import os, sys, time, types, threading, importlib.util
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location(
    "skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  [PASS]", name)
    else: FAIL += 1; print("  [FAIL]", name, "->", detail)


def make_state():
    return types.SimpleNamespace(
        lock=threading.Lock(), recent_interactions=[], pending_invites={})


def set_ago(state, cn, kind, seconds):
    """直接塞入一条 N 秒前的事件。"""
    state.recent_interactions.append({"cn": cn, "kind": kind, "ts": time.time() - seconds})


print("=" * 60); print("近期互动记忆 · 防重复发起"); print("=" * 60)

# 1. 空状态：prompt 为空、不判重
st = make_state()
check("无互动 -> prompt 为空", M.build_recent_interaction_prompt(st) == "")
check("无互动 -> 不判重", M.is_duplicate_initiate(st, "抱抱") is False)

# 2. 接受玩家抱抱后，prompt 应包含动作名与"接受了珂珂发起的"
st = make_state()
M.record_interaction(st, "抱抱", "accepted")
p = M.build_recent_interaction_prompt(st)
check("接受抱抱 -> prompt 含「抱抱」", "抱抱" in p, p)
check("接受抱抱 -> 写清是珂珂发起", "接受了珂珂发起的" in p, p)
check("接受抱抱 -> 有'不是新请求'提示", "不是新请求" in p, p)

# 3. 主动发起后，prompt 写"主动发起"
st = make_state()
M.record_interaction(st, "抱抱", "initiated")
p = M.build_recent_interaction_prompt(st)
check("主动发起 -> prompt 写主动发起", "主动发起的" in p, p)

# 4. 核心场景：刚接受抱抱（19s，无新邀请），AI 又要主动发起抱抱 -> 判重
st = make_state()
set_ago(st, "抱抱", "accepted", 19)
check("刚抱过19s无新邀请 -> 判重复", M.is_duplicate_initiate(st, "抱抱") is True)

# 5. 但屏幕上又出现抱抱邀请图标（玩家再次发起）-> 不拦，交给阶段3
st = make_state()
set_ago(st, "抱抱", "accepted", 19)
st.pending_invites = {"accept_hug": {"action_cn": "抱抱", "first_seen": time.time()}}
check("刚抱过但有新邀请图标 -> 不拦", M.is_duplicate_initiate(st, "抱抱") is False)

# 6. 超过去重窗口（默认45s）-> 放行（隔一会儿再抱是正常的）
st = make_state()
set_ago(st, "抱抱", "accepted", 60)
check("60s前抱过 -> 不拦(窗口外)", M.is_duplicate_initiate(st, "抱抱") is False)

# 7. 刚抱过，但要发起的是另一个动作 -> 不拦（只去重同动作）
st = make_state()
set_ago(st, "抱抱", "accepted", 10)
check("刚抱过但发起摸头 -> 不拦", M.is_duplicate_initiate(st, "摸头") is False)

# 8. 兜底关闭（窗口=0）-> 即使刚做过也不拦
st = make_state()
set_ago(st, "抱抱", "accepted", 5)
orig = M.INITIATE_DEDUP_SECONDS
M.INITIATE_DEDUP_SECONDS = 0.0
check("SKY_DEDUP_INITIATE_SEC=0 -> 不拦", M.is_duplicate_initiate(st, "抱抱") is False)
M.INITIATE_DEDUP_SECONDS = orig

# 9. 超出记忆窗口(180s)的事件被清理（只看事件行，避免模板示例文字"又抱抱"干扰）
st = make_state()
set_ago(st, "抱抱", "accepted", 200)
M.record_interaction(st, "摸头", "initiated")  # 触发一次清理
check("200s前的抱抱过期 -> 从记忆清除",
      all(e["cn"] != "抱抱" for e in st.recent_interactions), str(st.recent_interactions))
check("刚发起的摸头保留", any(e["cn"] == "摸头" for e in st.recent_interactions))
p = M.build_recent_interaction_prompt(st)
event_lines = [l for l in p.splitlines() if l.strip().startswith("·")]
check("过期抱抱 -> 不在事件行", not any("「抱抱」" in l for l in event_lines), str(event_lines))
check("刚发起的摸头 -> 在事件行", any("「摸头」" in l for l in event_lines), str(event_lines))

# 10. 同动作接受后又发起：事件行只保留最近一次（主动发起那次）
st = make_state()
M.record_interaction(st, "抱抱", "accepted")
time.sleep(0.01)
M.record_interaction(st, "抱抱", "initiated")
p = M.build_recent_interaction_prompt(st)
event_lines = [l for l in p.splitlines() if l.strip().startswith("·")]
check("同动作两次 -> 事件行只列一行",
      sum("「抱抱」" in l for l in event_lines) == 1, str(event_lines))
check("同动作两次 -> 取最近(主动发起)", "主动发起的" in p, p)

# 11. 时间描述：<60s 显示"约N秒前"
st = make_state()
set_ago(st, "抱抱", "accepted", 20)
p = M.build_recent_interaction_prompt(st)
check("20s -> 显示约N秒前", "秒前" in p, p)
st = make_state()
set_ago(st, "抱抱", "accepted", 90)
p = M.build_recent_interaction_prompt(st)
check("90s -> 显示约N分钟前", "分钟前" in p, p)

# 12. 最多保留 8 条（防异常堆积）
st = make_state()
for i in range(12):
    M.record_interaction(st, f"动作{i}", "initiated")
check("事件上限 8 条", len(st.recent_interactions) == 8, f"n={len(st.recent_interactions)}")

# 13. _execute_reply 好友树分支的判重条件：普通 [ACT] 被拦，[ACT!] 有意再做放行
st = make_state()
M.record_interaction(st, "抱抱", "accepted")
time.sleep(0.01)
def _should_skip(reply, act):
    # 复刻 _execute_reply 里的条件：act not in force_acts and is_duplicate_initiate(...)
    return act not in M.extract_force_acts(reply) and M.is_duplicate_initiate(st, act)
check("刚抱过·普通[ACT] -> 跳过(防OCR误读)",
      _should_skip("[CHAT]抱一下[/CHAT][ACT]抱抱[/ACT]", "抱抱"))
check("刚抱过·[ACT!] -> 不跳过(星河自己想/玩家明确要求)",
      not _should_skip("[CHAT]再抱一个[/CHAT][ACT!]抱抱[/ACT]", "抱抱"))
check("玩家明确说再抱一次·AI用[ACT!] -> 能再抱",
      not _should_skip("[ACT!]抱抱[/ACT]", "抱抱"))

# 14. 软件按键 SendInput 失败重试（PcSkyController._soft_event）
try:
    sspec = importlib.util.spec_from_file_location(
        "skymcp", os.path.join(ROOT, "sky-mcp-server.py"))
    S = importlib.util.module_from_spec(sspec)
    sys.modules["skymcp"] = S          # @dataclass 处理时要从 sys.modules 找模块，必须先注册
    sspec.loader.exec_module(S)
    srv = S.PcSkyController.__new__(S.PcSkyController)   # 绕过 __init__，不连硬件
    srv.SOFT_SEND_RETRIES = 3
    class FlakyInp:
        def __init__(self, fail_times):
            self.fail_times = fail_times; self.calls = 0
        def keyDown(self, k):
            self.calls += 1
            return self.calls > self.fail_times        # 前 fail_times 次返回 False
    fi = FlakyInp(2)
    check("软件键：前2次失败、第3次成功 -> 重试成功",
          srv._soft_event(fi, fi.keyDown, "f") is True and fi.calls == 3)
    fi2 = FlakyInp(99)
    check("软件键：一直失败 -> 重试3次后放弃",
          srv._soft_event(fi2, fi2.keyDown, "f") is False and fi2.calls == 3)
    class NoneInp:
        def keyDown(self, k):
            return None
    ni = NoneInp()
    check("软件键：pyautogui 返回 None -> 当成功、不重试",
          srv._soft_event(ni, ni.keyDown, "f") is True)
except Exception as e:
    check("软键盘重试测试可加载并执行", False, repr(e))

print("=" * 60)
print(f"结果: PASS={PASS} FAIL={FAIL}")
sys.exit(0 if FAIL == 0 else 1)
