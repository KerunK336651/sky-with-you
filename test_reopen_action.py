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

# ---------- 10. 弹奏中「乐器界面消失」看门狗（song_ui_lost，纯决策） ----------
# 2026-11-13 真机：界面被关掉但 SongPlayer 还在发音符，song_status 仍是 playing，
# 光看状态发现不了 -> 用 YOLO 的 instrument 检测结果兜底。
check("看门狗：模型没有 instrument 类 -> 不判定（宁可不误停）",
      M.song_ui_lost(1000.0, 900.0, lost_sec=10, class_online=False) is False)
check("看门狗：还没建立基线（last_seen=0）-> 不判定",
      M.song_ui_lost(1000.0, 0, lost_sec=10, class_online=True) is False)
check("看门狗：刚看到过（5s < 10s）-> 不停",
      M.song_ui_lost(1000.0, 995.0, lost_sec=10, class_online=True) is False)
check("看门狗：正好到阈值（10s）-> 停（边界取 >=）",
      M.song_ui_lost(1000.0, 990.0, lost_sec=10, class_online=True) is True)
check("看门狗：远超阈值（30s）-> 停",
      M.song_ui_lost(1000.0, 970.0, lost_sec=10, class_online=True) is True)
check("看门狗：lost_sec 传 1（自定义阈值）生效",
      M.song_ui_lost(1000.0, 998.5, lost_sec=1, class_online=True) is True)
check("看门狗：默认阈值来自 SONG_UI_LOST_SEC",
      M.song_ui_lost(1000.0, 1000.0 - M.SONG_UI_LOST_SEC, class_online=True) is True)
check("看门狗：阈值下限被夹到 1 秒（不会 0 秒就停）",
      M.song_ui_lost(1000.0, 999.5, lost_sec=0, class_online=True) is False)

# ---------- 12. 播放卡死检测（song_stalled，纯决策） ----------
check("卡死：played_ms 有前进 -> 不停",
      M.song_stalled(1000.0, 5200, 5000, 995.0, stall_sec=8) is False)
check("卡死：没前进但未到阈值（5s < 8s）-> 不停",
      M.song_stalled(1000.0, 5000, 5000, 995.0, stall_sec=8) is False)
check("卡死：没前进且到阈值（8s）-> 停",
      M.song_stalled(1000.0, 5000, 5000, 992.0, stall_sec=8) is True)
check("卡死：基线为 None（刚开弹）-> 不停",
      M.song_stalled(1000.0, 0, -1, None, stall_sec=8) is False)
check("卡死：阈值下限夹到 2 秒（不会 0 秒就停）",
      M.song_stalled(1000.0, 5000, 5000, 999.5, stall_sec=0) is False)
check("卡死：默认阈值来自 SONG_STALL_SEC",
      M.song_stalled(1000.0, 5000, 5000, 1000.0 - M.SONG_STALL_SEC) is True)

# ---------- 13. 弹奏中 CHAT 攒着、结束后补发 ----------
import threading  # noqa: E402

_orig_send = M._send_msg
M._song_session_active = True
M._song_deferred_chat.clear()
M._song_deferred_dropped = 0
_r1 = M._send_msg(None, None, "第一句", None)      # 弹奏中：不该真发，只入队
check("弹奏中 _send_msg 返回 True（当作已受理）", _r1 is True)
check("弹奏中消息进了攒话队列", M._song_deferred_chat == ["第一句"])
for _i in range(5):
    M._send_msg(None, None, "溢出%d" % _i, None)
check("队列有上限（最多 %d 条）" % M.SONG_DEFERRED_MAX,
      len(M._song_deferred_chat) == M.SONG_DEFERRED_MAX)
check("溢出有计数", M._song_deferred_dropped > 0)

# 会话结束后由 action 线程补发
_sent = []
M._send_msg = lambda det, sess, msg, state: (_sent.append(msg), True)[1]


class _StubState:
    def __init__(self):
        self.lock = threading.Lock()
        self.recent_seen = []
        self.sent_history = []


_st = _StubState()
M._song_session_active = False
M._flush_song_deferred_chat(None, _st, None)
check("会话结束后攒下的消息被补发", len(_sent) == M.SONG_DEFERRED_MAX)
check("补发后队列清空", M._song_deferred_chat == [])
check("补发后写进 sent_history", len(_st.sent_history) == M.SONG_DEFERRED_MAX)
check("丢弃计数在补发时清零", M._song_deferred_dropped == 0)

# 弹奏中不允许补发（否则又会在演奏时开面板）
_sent.clear()
M._song_deferred_chat.append("演奏中的不该发")
M._song_session_active = True
M._flush_song_deferred_chat(None, _st, None)
check("弹奏中不补发（消息留在队列里）",
      _sent == [] and M._song_deferred_chat == ["演奏中的不该发"])
M._song_session_active = False
M._song_deferred_chat.clear()
M._send_msg = _orig_send

# ---------- 11. 收界面/停曲必须是 best-effort（不许抛异常打死监控线程） ----------
class _RaisingSess:
    pass


_orig_key = M._key
_orig_mcp = M.mcp
M._key = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("前台校验拒绝"))
M.mcp = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("MCP 拒绝"))
try:
    _ok = M._song_close_instrument(_RaisingSess())
    check("收界面失败不抛异常（返回 False）", _ok is False)
    _ok2 = M._song_stop_and_close(_RaisingSess())
    check("停曲+收界面全失败也不抛异常", _ok2 is False)
finally:
    M._key = _orig_key
    M.mcp = _orig_mcp

# ---------- 14. 乐器守卫「只关我们自己开的」（2026-11-13 修 11-11 的 P1） ----------
# 以前玩家手动打开乐器时 session_active=False，守卫 2 帧后 ESC 掉 -> 玩家没法自己弹琴。
_ig = M.instrument_guard_allowed
check("守卫：弹琴会话中 -> 不许关",
      _ig(session_active=True, sending=False, chat_open=False,
          opened_by_us_at=0, now=100.0) is False)
check("守卫：发消息中 -> 不许关",
      _ig(False, True, False, 0, 100.0) is False)
check("守卫：聊天面板开着 -> 不许关",
      _ig(False, False, True, 0, 100.0) is False)
check("守卫：**玩家手动开的乐器**（我们从没按过槽位键）-> 不许关",
      _ig(False, False, False, opened_by_us_at=0, now=100.0) is False)
check("守卫：我们自己刚开的（5s 前）-> 允许关",
      _ig(False, False, False, opened_by_us_at=95.0, now=100.0) is True)
check("守卫：我们开的但已超过窗口（200s > 120s）-> 不许关",
      _ig(False, False, False, opened_by_us_at=-100.0, now=100.0) is False)
check("守卫：窗口边界取 >=",
      _ig(False, False, False, opened_by_us_at=1000.0, now=1120.0,
          own_window=120) is True)
check("守卫：own_enabled=False（回退旧行为）-> 允许关",
      _ig(False, False, False, 0, 100.0, own_enabled=False) is True)
check("守卫：opened_by_us_at 是脏值也不炸",
      _ig(False, False, False, "bad", 100.0) is False)

# ---------- 15. 弹奏会话代际号：被取代的旧监视器必须立刻退场 ----------
M._song_gen = 7
M._song_session_active = False
M._song_stop_monitor(None, None, None, None, "旧歌", 6)     # 旧会话（gen 6 < 7）
check("旧会话监视器：不接管 session_active",
      M._song_session_active is False)
M._song_gen = 0          # 复位，别影响后面的用例

# ---------- 16. stamp_model 的最小安全守卫 ----------
_sm_path = os.path.join(ROOT, "tools", "stamp_model.py")
_spec = importlib.util.spec_from_file_location("stamp_model", _sm_path)
SM = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(SM)
    check("stamp_model：不存在 -> 拒绝", SM.check_pt_safe(os.path.join(ROOT, "no_such.pt")))
    _bad = os.path.join(ROOT, "_tmp_pt_bad.txt")
    open(_bad, "wb").write(b"not a torch file")
    check("stamp_model：非 .pt 后缀 -> 拒绝", SM.check_pt_safe(_bad))
    _bad2 = os.path.join(ROOT, "_tmp_pt_bad.pt")
    open(_bad2, "wb").write(b"not a torch file")
    check("stamp_model：头部不是 zip -> 拒绝", SM.check_pt_safe(_bad2))
    _good = os.path.join(ROOT, "_tmp_pt_good.pt")
    open(_good, "wb").write(b"PK\x03\x04" + b"\x00" * 16)
    check("stamp_model：zip 头 + 项目内 -> 放行", SM.check_pt_safe(_good) is None)
    _outside = os.path.join(os.environ.get("TEMP", ROOT), "_tmp_pt_outside.pt")
    try:
        open(_outside, "wb").write(b"PK\x03\x04" + b"\x00" * 16)
        _r = SM.check_pt_safe(_outside)
        check("stamp_model：项目外默认拒绝", isinstance(_r, str) and "--anywhere" in _r)
        check("stamp_model：项目外加 --anywhere 放行", SM.check_pt_safe(_outside, anywhere=True) is None)
    finally:
        for _p in (_bad, _bad2, _good, _outside):
            try:
                os.remove(_p)
            except OSError:
                pass
except Exception as _e:
    check("stamp_model 可加载（缺失 torch 时跳过）", False, str(_e))

# ---------- 17. panel_detector 的 MCP token 默认值 ----------
_pd_path = os.path.join(ROOT, "panel_detector.py")
_pd_src = open(_pd_path, encoding="utf-8").read()
check("panel_detector：不再写死 1234", 'default="1234"' not in _pd_src)
check("panel_detector：默认读 mcp_token.txt",
      "mcp_token.txt" in _pd_src and "_default_mcp_token" in _pd_src)

print("=" * 60)
print(f"结果: PASS={PASS} FAIL={FAIL}")
sys.exit(0 if FAIL == 0 else 1)
