"""离线测试守门 reopen 纯函数 _guard_should_reopen。
重点验证：HOME（遇境）场景面板关闭后能触发 reopen（真机 2026-10-03 卡住根因）。
运行：py test_guard_reopen.py
"""
import importlib.util
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("skyloop", "sky-loop-v7.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _snap(screen, confirm=False, tree=False):
    return SimpleNamespace(
        screen=screen,
        confirm_dialog=SimpleNamespace(value=confirm),
        friend_tree=SimpleNamespace(value=tree))


t0 = 1000.0
LONG_AGO = t0 - 100   # 远超 POST_ACTION_GRACE / REOPEN_COOLDOWN


def check(desc, expect, **kw):
    got = m._guard_should_reopen(**kw)
    assert got is expect, f"{desc}: 期望 {expect}，实际 {got}"
    print(f"  [PASS] {desc}")


# 1. HOME 场景、面板关、无占用、时间足够 → 应 reopen
check("HOME 遇境面板关闭后应 reopen", True,
      snap=_snap(m.Screen.HOME), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 2. IN_WORLD 场景同样应 reopen
check("IN_WORLD 面板关闭后应 reopen", True,
      snap=_snap(m.Screen.IN_WORLD), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 3. LOADING 场景 → 不 reopen
check("LOADING 不 reopen", False,
      snap=_snap(m.Screen.LOADING), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 4. 面板刚关（PANEL_REOPEN_DELAY 内）→ 不 reopen
check("面板刚关 1.2s 内不 reopen", False,
      snap=_snap(m.Screen.HOME), panel_state=False,
      panel_closed_since=t0 - 0.5, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 5. 确认弹窗存在 → 不 reopen
check("确认弹窗存在不 reopen", False,
      snap=_snap(m.Screen.HOME, confirm=True), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 6. 好友树存在 → 不 reopen
check("好友树存在不 reopen", False,
      snap=_snap(m.Screen.HOME, tree=True), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 7. 动作刚结束（POST_ACTION_GRACE 内）→ 不 reopen
check("动作刚结束 8s 内不 reopen", False,
      snap=_snap(m.Screen.HOME), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=t0 - 3, last_reopen=LONG_AGO)

# 8. busy → 不 reopen
check("busy 不 reopen", False,
      snap=_snap(m.Screen.HOME), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=True, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 9. 面板开着 → 不 reopen
check("面板开着不 reopen", False,
      snap=_snap(m.Screen.HOME), panel_state=True,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=True, last_action=LONG_AGO, last_reopen=LONG_AGO)

# 10. action_q 非空 → 不 reopen
check("队列非空不 reopen", False,
      snap=_snap(m.Screen.HOME), panel_state=False,
      panel_closed_since=t0 - 10, t0=t0, busy=False, sending=False,
      action_empty=False, last_action=LONG_AGO, last_reopen=LONG_AGO)

print("\n全部通过")
