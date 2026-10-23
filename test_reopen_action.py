# -*- coding: utf-8 -*-
"""
test_reopen_action.py — 动作后主动重开面板的门控逻辑测试（离线，不需游戏/硬件）

验证 _reopen_panel_after_action 在该重开时重开、不该重开时绝不按 C：
  开关关闭 / 非游戏场景 / 弹窗 / 好友树仍在 / 回遇境冷却 / 面板已开 -> 不重开；
  正常场景且面板关 -> 重开一次。

用法:  py test_reopen_action.py
"""
import os, sys, importlib.util, types
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location(
    "skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  [PASS]", name)
    else: FAIL += 1; print("  [FAIL]", name, "->", detail)


class _Flag:
    def __init__(self, v=False): self.value = v


class FakeSnap:
    def __init__(self, screen, confirm=False, friend=False):
        self.screen = screen
        self.confirm_dialog = _Flag(confirm)
        self.friend_tree = _Flag(friend)


class FakeDet:
    def __init__(self, snap):
        self.world = self
        self._snap = snap
    def snapshot(self):
        return self._snap


def make_env(screen=M.Screen.IN_WORLD, confirm=False, friend=False,
             panel_state=True, gohome_age=9999, switch=True):
    det = FakeDet(FakeSnap(screen, confirm, friend))
    state = types.SimpleNamespace()
    sess = None
    opened = {"n": 0}
    # 保存并替换模块级依赖
    orig = {
        "panel_open": M.panel_open, "_open_panel": M._open_panel,
        "REOPEN": M.REOPEN_PANEL_AFTER_ACTION, "gohome": M._last_gohome_time,
    }
    M.panel_open = lambda d: panel_state
    M._open_panel = lambda d, s, st: opened.__setitem__("n", opened["n"] + 1) or True
    M.REOPEN_PANEL_AFTER_ACTION = switch
    M._last_gohome_time = M.time.time() - gohome_age
    return det, state, sess, opened, orig


def restore(orig):
    M.panel_open = orig["panel_open"]
    M._open_panel = orig["_open_panel"]
    M.REOPEN_PANEL_AFTER_ACTION = orig["REOPEN"]
    M._last_gohome_time = orig["gohome"]


print("=" * 60); print("动作后主动重开面板 · 门控"); print("=" * 60)

# 1. 正常场景、面板关 -> 重开一次
det, state, sess, opened, orig = make_env(panel_state=False)
M._reopen_panel_after_action(det, sess, state)
check("正常场景面板关 -> 重开1次", opened["n"] == 1, f"n={opened['n']}")
restore(orig)

# 2. 面板已开 -> 不重开
det, state, sess, opened, orig = make_env(panel_state=True)
M._reopen_panel_after_action(det, sess, state)
check("面板已开 -> 不重开", opened["n"] == 0, f"n={opened['n']}")
restore(orig)

# 3. 开关关闭 -> 不重开
det, state, sess, opened, orig = make_env(panel_state=False, switch=False)
M._reopen_panel_after_action(det, sess, state)
check("开关SKY_REOPEN_AFTER_ACTION=0 -> 不重开", opened["n"] == 0, f"n={opened['n']}")
restore(orig)

# 4. 非游戏场景（LOADING）-> 不重开
det, state, sess, opened, orig = make_env(screen=M.Screen.LOADING, panel_state=False)
M._reopen_panel_after_action(det, sess, state)
check("LOADING -> 不重开", opened["n"] == 0, f"n={opened['n']}")
restore(orig)

# 5. 确认弹窗在 -> 不重开
det, state, sess, opened, orig = make_env(confirm=True, panel_state=False)
M._reopen_panel_after_action(det, sess, state)
check("确认弹窗在 -> 不重开", opened["n"] == 0, f"n={opened['n']}")
restore(orig)

# 6. 好友树仍在 -> 不重开
det, state, sess, opened, orig = make_env(friend=True, panel_state=False)
M._reopen_panel_after_action(det, sess, state)
check("好友树仍在 -> 不重开", opened["n"] == 0, f"n={opened['n']}")
restore(orig)

# 7. 回遇境冷却内 -> 不重开
det, state, sess, opened, orig = make_env(panel_state=False, gohome_age=5)
M._reopen_panel_after_action(det, sess, state)
check("回遇境冷却内 -> 不重开", opened["n"] == 0, f"n={opened['n']}")
restore(orig)

# 8. HOME 场景面板关 -> 重开
det, state, sess, opened, orig = make_env(screen=M.Screen.HOME, panel_state=False)
M._reopen_panel_after_action(det, sess, state)
check("HOME场景面板关 -> 重开", opened["n"] == 1, f"n={opened['n']}")
restore(orig)

# 9. 冷却刚过（16s）-> 重开
det, state, sess, opened, orig = make_env(panel_state=False, gohome_age=16)
M._reopen_panel_after_action(det, sess, state)
check("回遇境冷却已过 -> 重开", opened["n"] == 1, f"n={opened['n']}")
restore(orig)

print("=" * 60)
print(f"结果: PASS={PASS} FAIL={FAIL}")
sys.exit(0 if FAIL == 0 else 1)
