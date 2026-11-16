# -*- coding: utf-8 -*-
"""
光遇互动图标采集器 · GUI 版（PyQt5 + 低级键盘钩子 + DXGI 实时截图 + 冻结帧保险）

【重要】请用同目录的 run_collect_gui.bat 启动（自动申请管理员权限）。

和命令行版 collect_interactions.py 功能一致，但有图形界面：
  - 左侧实时预览：直接看到当前抓到的游戏画面，确认要截的内容；
  - 右侧类别按钮：每个按钮显示「中文名 + 英文类名 + 已采张数」，选中高亮，
    鼠标可点，也可以继续用键盘（游戏全屏时只能用键盘 + 提示音）。

切换 / 截图按键（游戏里按即可，不拦截游戏本身的按键）：
  小键盘 1-4 = 选当前页的类（需开 NumLock）；小键盘 0 = 快速切负样本
  F6 = 翻到下一页（本轮 2 页）；F8 = 下一个类；F7 = 上一个类
  F9 = 截当前类 1 张（画面没变化会被跳过）
  F10 = 开/关连拍（每 1.5s 一张，期间务必走动/转镜头换画面）
  F12 = 退出，并打印每类数量
鼠标：点类别按钮、翻页按钮、截图 / 连拍按钮。

截图为游戏窗口客户区，存到 D:\\光遇互动采集\\<英文类名>\\
（无 D 盘时存到桌面 sky_interaction_raw）。
"""
import os
import sys
import time
import ctypes
import queue
import threading
from ctypes import wintypes
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cv2
import numpy as np
import winsound

from PyQt5 import QtCore, QtGui, QtWidgets

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()

if os.path.isdir("D:\\"):
    SAVE_ROOT = r"D:\光遇互动采集"
else:
    SAVE_ROOT = os.path.join(os.path.expanduser("~"), "Desktop", "sky_interaction_raw")

BURST_INTERVAL = 1.5
FREEZE_DIFF = 0.5          # 64x36 灰度上，相邻两张平均像素差 < 此值视为画面未变化/冻结
CAP_FPS_SLEEP = 0.03       # 后台抓帧间隔（约 30fps，DXGI 无新帧时自动返回，几乎不占资源）
PREVIEW_MS = 60            # 界面实时预览刷新间隔（约 16fps）
POLL_MS = 30               # 键盘事件轮询间隔

# ── 类别分页（英文类名, 中文名）─────────────────────────────────────────
# 2026-10-31 按《补采清单_20261031.md》重排为 4 页：
#   第1页：本轮新增 6 个正类（v7 已有 116~233 张，继续补多样性）
#   第2页：P0 老类补采 A（9 类，真机反馈识别低或样本偏少）
#   第3页：P0/P1 老类补采 B（8 类）
#   第4页：negative 困难负样本（小键盘 0 也可快速切到）
PAGES = [
    [  # 第 1 页 · 新增正类
        ("instrument", "乐器界面"),
        ("settings_menu", "设置菜单"),
        ("butterfly_lure", "引蝶"),
        ("action_cancel", "持续动作取消"),
        ("princess_hug", "公主抱"),
        ("duo_dance", "双人舞"),
    ],
    [  # 第 2 页 · P0 老类补采 A
        ("accept_hug", "抱抱"),
        ("accept_high_five", "击掌"),
        ("follow", "跟随"),
        ("focus", "聚焦"),
        ("seat", "打坐"),
        ("mutual_light", "互火"),
        ("hand_icon", "牵手"),
        ("bear_hug", "熊抱"),
        ("wing_light", "光翼"),
    ],
    [  # 第 3 页 · P0/P1 老类补采 B
        ("curtain_call", "谢幕"),
        ("fancy_high_five", "花式击掌"),
        ("head_pat", "摸头"),
        ("play_fight", "打闹"),
        ("spin_dance", "共舞"),
        ("accept_fist_bump", "碰拳"),
        ("light", "点火"),
        ("handshake", "握手"),
    ],
    [  # 第 4 页 · 补差类 + 负样本
        ("chat_input", "聊天框"),
        ("open_tree_icon", "好友树"),
        ("accept_carry", "背背"),
        ("shoulder", "搭肩"),
        ("whisper", "耳语"),
        ("negative", "负样本"),
    ],
]
PAGE_TITLES = ["新增正类", "P0 老类补采 A", "P0/P1 老类补采 B", "补差类 + 负样本"]

# 每类"本轮还要补多少"（2026-11-02 改：从"总目标"改为"本轮新增目标"，
# 按钮显示 本轮已采/目标；启动时统计采集目录里已有的图片数，到目标会响铃提醒）
# 2026-11-02 晚：按用户要求"全类补齐、攒够一次训练"，把 28 类 + negative 全部加上。
TARGETS = {
    # 第 1 页 · 新增正类
    "instrument": 180, "settings_menu": 200, "butterfly_lure": 180,
    "action_cancel": 160, "princess_hug": 80, "duo_dance": 80,
    # 第 2 页 · P0 老类补采 A
    "accept_hug": 250, "accept_high_five": 200, "follow": 200, "focus": 200,
    "seat": 180, "mutual_light": 180, "hand_icon": 200, "bear_hug": 200,
    "wing_light": 200,
    # 第 3 页 · P0/P1 老类补采 B
    "curtain_call": 200, "fancy_high_five": 200, "head_pat": 150,
    "play_fight": 200, "spin_dance": 200, "accept_fist_bump": 150,
    "light": 160, "handshake": 100,
    # 第 4 页 · 补差类 + 负样本
    "chat_input": 80, "open_tree_icon": 80, "accept_carry": 80,
    "shoulder": 80, "whisper": 80, "negative": 300,
}

# 每类"要拍什么"的中文提示（选中时显示在左下状态栏）
HINTS = {
    "instrument": "要拍：乐器界面（弹琴中/刚打开/不同乐器）、面板开与关、近/远、白天/夜晚",
    "settings_menu": "要拍：误开的设置菜单（主菜单/子页）、不同地图、面板开/关、半透明淡入淡出",
    "butterfly_lure": "要拍：引蝶图标（近/中/远）、白天/夜晚/举火、单人/多人同屏",
    "action_cancel": "要拍：持续动作取消按钮（背背/牵手/公主抱中）、不同地图/光照、被人物遮挡",
    "princess_hug": "要拍：公主抱邀请图标（近/中/远）、夜晚/雨天、多人同屏、半透明",
    "duo_dance": "要拍：双人舞邀请图标（近/中/远）、不同地图/光照、多人同屏",
    "accept_hug": "要拍：抱抱邀请图标（近/中/远）、白天/夜晚/举火、单人/双人/多人重叠、面板开/关、半透明/被遮挡",
    "accept_high_five": "要拍：击掌邀请图标（近/中/远）、不同地图/光照、多人同屏、面板开/关",
    "follow": "要拍：跟随图标（好友树打开后头顶变跟随）、近/远、不同地图、被人物遮挡",
    "focus": "要拍：聚焦图标（近/远、不同场景）、面板开/关、半透明",
    "seat": "要拍：打坐/座位图标（近/远）、不同地图/光照、被遮挡",
    "mutual_light": "要拍：星盘互火图标（近/远）、不同地图、多人星盘",
    "hand_icon": "要拍：牵手图标（对方伸手瞬间；近/中/远、正/侧/斜）、不同地图/光照/多人、面板开/关",
    "bear_hug": "要拍：熊抱邀请图标（近/中/远）、夜晚/雨天/举火、多人同屏、半透明/被遮挡",
    "wing_light": "要拍：光翼（小金人）拾取图标（近/远、不同地图）、面板开/关、被遮挡；注意无翼号不要真捡",
    "curtain_call": "要拍：谢幕邀请图标（近/中/远）、夜晚/举火、多人同屏",
    "fancy_high_five": "要拍：花式击掌邀请图标（近/中/远）、不同地图/光照、多人同屏",
    "head_pat": "要拍：摸头邀请图标（近/中/远）、不同地图/光照、多人同屏、半透明",
    "play_fight": "要拍：打闹邀请图标（近/中/远）、夜晚/雨天、多人同屏、移动中",
    "spin_dance": "要拍：共舞邀请图标（近/中/远）、不同地图/光照、多人同屏、半透明",
    "accept_fist_bump": "要拍：碰拳邀请图标（近/中/远）、不同地图/光照、多人同屏",
    "light": "要拍：点火图标（近/远）、不同地图/光照、面板开/关",
    "handshake": "要拍：握手邀请图标（近/中/远）、不同地图/光照、多人同屏",
    "chat_input": "要拍：聊天输入框/历史面板（开/关、输入态/非输入态、不同地图、半透明）",
    "open_tree_icon": "要拍：好友树入口图标（近/中/远）、不同地图/光照、被人物遮挡、多人同屏",
    "accept_carry": "要拍：背背邀请图标（近/中/远）、不同地图/光照、多人同屏、半透明",
    "shoulder": "要拍：搭肩邀请图标（近/中/远）、不同地图/光照、多人同屏",
    "whisper": "要拍：耳语邀请图标（近/中/远）、不同地图/光照、多人同屏",
    "negative": "要拍：圆形 F 但不是互动的图标——星盘点火/传送、衣柜换装、纸船/留影、爱心牌、乐器音符、好友树内部按钮、别人头顶 F；空标签不画框",
}
FLAT = [t for _page in PAGES for t in _page]
EN_TO_CN = {en: cn for en, cn in FLAT}


def count_existing():
    """统计采集目录里每类已有图片数（用于按钮上的"已采/目标"，重启不清零）。"""
    out = {}
    for en, _ in FLAT:
        d = os.path.join(SAVE_ROOT, en)
        n = 0
        try:
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                        n += 1
        except OSError:
            pass
        out[en] = n
    return out
NEGATIVE_EN = "negative"
# 第 2 页的 negative 是负样本类（按钮灰色显示）；小键盘 0 可快速切到它。
NEGATIVE_SET: set = {"negative"}

# 小键盘数字（开 NumLock）-> 当前页内索引（0-8）；N0 单独快速切负样本
NUMPAD_DIGIT = {0x61: 0, 0x62: 1, 0x63: 2, 0x64: 3, 0x65: 4,
                0x66: 5, 0x67: 6, 0x68: 7, 0x69: 8}
VK_NUMPAD0 = 0x60
VK_F6, VK_F7, VK_F8, VK_F9, VK_F10, VK_F12 = 0x75, 0x76, 0x77, 0x78, 0x79, 0x7B

# 显式声明 64 位钩子相关原型，避免句柄被截断
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


def find_game_window():
    """动态找“光·遇”窗口，返回客户区 {left,top,width,height}（不含标题栏）。"""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if "光·遇" not in title:
            return True
        # 排除控制台（cmd/PowerShell 标题路径里可能带“光遇”字样）
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
            return True
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 500 or h <= 400:
            return True
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        found.append((hwnd, title, w, h, pt.x, pt.y))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return None
    found.sort(key=lambda x: (x[1] != "光·遇", -x[2]))
    _, title, w, h, x, y = found[0]
    return {"left": x, "top": y, "width": w, "height": h, "title": title}


# ===================== DXGI 截屏坐标系适配（与 panel_detector 同实现） =====================

def _output_desktop_rect(cam):
    """取 bettercam/dxcam 某输出设备的绝对桌面矩形 (l,t,r,b)；拿不到返回 None。"""
    try:
        out = getattr(cam, "_output", None)
        hm = getattr(out, "hmonitor", None) if out is not None else None
        if not hm:
            return None

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(int(hm)),
                                                    ctypes.byref(mi)):
            return None
        return (int(mi.rcMonitor.left), int(mi.rcMonitor.top),
                int(mi.rcMonitor.right), int(mi.rcMonitor.bottom))
    except Exception:
        return None


def _enum_dxgi_outputs(mod, max_outputs=8):
    """枚举输出，返回 [(cam, 绝对矩形), ...]。create 对同一输出返回单例，这里不要 release。"""
    outs = []
    for idx in range(max_outputs):
        try:
            cam = mod.create(output_idx=idx, output_color="BGR")
        except Exception:
            break
        if cam is None:
            break
        rect = _output_desktop_rect(cam)
        if rect is None:
            break
        outs.append((cam, rect))
    return outs


def _pick_output(outputs, left, top, right, bottom):
    """挑出完全包含该绝对矩形的第一块输出；跨屏/被切边返回 None。"""
    for item in (outputs or []):
        obj, rect = item[0], item[1]
        if rect[0] <= left and rect[1] <= top and right <= rect[2] and bottom <= rect[3]:
            return item
    return None


def _to_output_relative(rect, left, top, right, bottom):
    """绝对桌面矩形 -> 相对该输出的矩形。"""
    return (int(left) - int(rect[0]), int(top) - int(rect[1]),
            int(right) - int(rect[0]), int(bottom) - int(rect[1]))


class ScreenGrabber:
    """DXGI 优先、GDI 兜底的抓帧器。grab(region) -> BGR ndarray | None(画面无变化)。"""

    def __init__(self):
        self.backend = "GDI"
        self.mod_name = None
        self.outputs = []
        self._sel = {}
        self._sct = None
        self._init_backend()

    def _init_backend(self):
        for mod_name in ("bettercam", "dxcam"):
            try:
                mod = __import__(mod_name)
                outs = _enum_dxgi_outputs(mod)
                if not outs:
                    raise RuntimeError("枚举不到带桌面矩形的输出设备")
                self.mod_name = mod_name
                self.outputs = outs
                self.backend = "DXGI"
                return
            except Exception as e:
                print("  %s 不可用：%s" % (mod_name, e))
        import mss as mss_lib
        self._sct = mss_lib.mss()
        self.backend = "GDI"

    def fallback_gdi(self):
        try:
            import mss as mss_lib
            self._sct = mss_lib.mss()
            self.backend = "GDI"
            self._sel = {}
        except Exception:
            pass

    def grab(self, region):
        reg = {"left": int(region["left"]), "top": int(region["top"]),
               "width": int(region["width"]), "height": int(region["height"])}
        l, t = reg["left"], reg["top"]
        r, b = l + reg["width"], reg["height"] + t
        if self.backend == "DXGI":
            key = (l, t, r, b)
            if key not in self._sel:
                hit = _pick_output(self.outputs, l, t, r, b)
                if hit is None:
                    raise RuntimeError("区域跨屏或被切边，DXGI 抓不了")
                self._sel[key] = hit
            cam, rect = self._sel[key]
            return cam.grab(region=_to_output_relative(rect, l, t, r, b))
        shot = self._sct.grab(reg)
        img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(img[:, :, :3])


class CaptureThread(threading.Thread):
    """后台持续抓帧并缓存最新帧，主线程按 F9 直接取，DXGI 静止返回 None 也有最近帧可用。"""

    def __init__(self, grabber):
        super().__init__(daemon=True)
        self.grabber = grabber
        self.lock = threading.Lock()
        self.latest = None
        self.region = None
        self._stop = threading.Event()
        self.grab_ok = 0
        self.grab_fail = 0
        self.fallback_count = 0

    def stop(self):
        self._stop.set()

    def get_latest(self):
        with self.lock:
            return None if self.latest is None else self.latest.copy()

    def run(self):
        last_win_check = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_win_check > 1.0:
                reg = find_game_window()
                if reg:
                    self.region = reg
                last_win_check = now
            if self.region:
                try:
                    frame = self.grabber.grab(self.region)
                    if frame is not None:
                        with self.lock:
                            self.latest = frame
                            self.grab_ok += 1
                        self.grab_fail = 0
                except Exception as e:
                    self.grab_fail += 1
                    if self.grab_fail >= 3 and self.grabber.backend == "DXGI" and self.fallback_count == 0:
                        self.fallback_count += 1
                        print("  [截图] DXGI 连续异常（%s），回退 GDI。" % e)
                        self.grabber.fallback_gdi()
                        self.grab_fail = 0
                    time.sleep(0.3)
            time.sleep(CAP_FPS_SLEEP)


def start_keyboard_hook(events):
    """开一个带消息循环的线程安装低级键盘钩子，把按下的 vk 放进 events。"""
    def proc(nCode, wParam, lParam):
        if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            try:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                events.put(kb.vkCode)
            except Exception:
                pass
        # 永远放行，不拦截游戏按键
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    cb = HOOKPROC(proc)

    def run():
        hmod = kernel32.GetModuleHandleW(None)
        hhk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, hmod, 0)
        if not hhk:
            events.put(-1)  # 安装失败标记
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hhk)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, cb


def beep(freq=1000, dur=40):
    try:
        winsound.Beep(freq, dur)
    except Exception:
        pass


def _gray_small(img):
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (64, 36)).astype(np.float32)


# 类别按钮样式：普通 / 选中 / 负样本
QSS = """
QMainWindow { background: #f5f6f8; }
QLabel { color: #222; }
QPushButton#typeBtn {
    background: #ffffff;
    border: 1px solid #d3d7de;
    border-radius: 8px;
    padding: 6px;
    min-width: 104px; min-height: 78px;
    font-size: 14px; color: #222;
}
QPushButton#typeBtn[negative="true"] { color: #6b7280; }
QPushButton#typeBtn[selected="true"] {
    background: #d9ebff;
    border: 2px solid #2f7de1;
    font-weight: bold;
}
QPushButton#bigBtn {
    background: #2f7de1; color: white; border: none; border-radius: 8px;
    min-height: 40px; font-size: 15px; font-weight: bold;
}
QPushButton#bigBtn:disabled { background: #a9c3e6; }
QPushButton#pageBtn {
    background: #eef1f5; border: 1px solid #d3d7de; border-radius: 6px;
    min-height: 32px; font-size: 13px;
}
QCheckBox { font-size: 13px; }
QFrame#warnBar { background: #fff3e0; border: 1px solid #f0c14b; border-radius: 6px; }
QLabel#warnText { color: #8a5a00; font-size: 13px; }
"""


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("光遇互动图标采集器")
        self.resize(1020, 640)

        # 每页起始全局索引
        self.page_start = []
        acc = 0
        for page in PAGES:
            self.page_start.append(acc)
            acc += len(page)

        self.counts = count_existing()
        self._hit = set()          # 已提醒过"达标"的类
        self.cur = 0
        self.burst = False
        self.dup_streak = 0
        self.last_saved_small = None
        self.last_burst = 0.0
        self.type_buttons = {}
        self._cleaned = False

        region = find_game_window()
        self.grabber = ScreenGrabber()
        self.backend = self.grabber.backend

        self.cap = CaptureThread(self.grabber)
        self.cap.start()
        t0 = time.time()
        while self.cap.get_latest() is None and time.time() - t0 < 4:
            time.sleep(0.1)

        self.events = queue.Queue()
        self._hook_t, self._hook_cb = start_keyboard_hook(self.events)
        time.sleep(0.15)

        self._build_ui()

        if not self.events.empty() and self.events.queue[0] == -1:
            QtWidgets.QMessageBox.critical(self, "钩子失败",
                                           "键盘钩子安装失败，请用 run_collect_gui.bat（管理员）启动。")
        if self.cap.get_latest() is None:
            self.status_text.setText("未抓到画面：确认游戏已进入、没有最小化。")

        # 预览刷新
        self.preview_timer = QtCore.QTimer(self)
        self.preview_timer.timeout.connect(self.update_preview)
        self.preview_timer.start(PREVIEW_MS)
        # 键盘 + 连拍轮询
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(POLL_MS)

        self.refresh_selection()
        self.refresh_status()

        # 启动时汇总本轮补采进度（目标在 TARGETS 里，按"本轮新增"算）
        _todo = sum(max(0, v - self.counts.get(en, 0)) for en, v in TARGETS.items() if v)
        _ok = sum(1 for en, v in TARGETS.items() if v and self.counts.get(en, 0) >= v)
        _n = sum(1 for v in TARGETS.values() if v)
        print("  本轮补采目标：%d 类 / 还差 %d 张（已达标 %d 类）"
              % (_n, _todo, _ok))
        print("  采集目录：%s（已有图片计入进度，重启不清零）" % SAVE_ROOT)

    # ── 界面构建 ──────────────────────────────────────────────────────
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setStyleSheet(QSS)
        root = QtWidgets.QHBoxLayout(central)

        # 左：预览 + 按钮 + 状态
        left = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("<b>实时预览</b>（确认要截的内容）")
        left.addWidget(title)
        self.preview_label = QtWidgets.QLabel("正在等待画面…")
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumSize(480, 270)
        self.preview_label.setStyleSheet("background:#1b1e23; color:#cfd4da; border-radius:8px;")
        left.addWidget(self.preview_label, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.shot_btn = QtWidgets.QPushButton("截图（F9）")
        self.shot_btn.setObjectName("bigBtn")
        self.shot_btn.clicked.connect(lambda: self.do_capture(manual=True))
        self.burst_btn = QtWidgets.QPushButton("连拍（F10）")
        self.burst_btn.setObjectName("bigBtn")
        self.burst_btn.setStyleSheet("background:#e08a2a;")
        self.burst_btn.clicked.connect(self.toggle_burst)
        btn_row.addWidget(self.shot_btn)
        btn_row.addWidget(self.burst_btn)
        left.addLayout(btn_row)

        self.status_text = QtWidgets.QLabel("")
        self.status_text.setWordWrap(True)
        left.addWidget(self.status_text)

        self.path_text = QtWidgets.QLabel("保存目录：%s" % SAVE_ROOT)
        self.path_text.setWordWrap(True)
        self.path_text.setStyleSheet("color:#555; font-size:12px;")
        left.addWidget(self.path_text)

        top_pin = QtWidgets.QCheckBox("窗口置顶（游戏无边框窗口时放旁边）")
        top_pin.toggled.connect(self.set_always_on_top)
        left.addWidget(top_pin)

        # 右：翻页 + 类别页
        right = QtWidgets.QVBoxLayout()
        top_row = QtWidgets.QHBoxLayout()
        self.page_label = QtWidgets.QLabel("")
        self.page_label.setStyleSheet("font-weight:bold;")
        prev_page = QtWidgets.QPushButton("◀ 上一页")
        prev_page.setObjectName("pageBtn")
        prev_page.clicked.connect(lambda: self.flip_page(-1))
        next_page = QtWidgets.QPushButton("下一页（F6）▶")
        next_page.setObjectName("pageBtn")
        next_page.clicked.connect(lambda: self.flip_page(1))
        top_row.addWidget(prev_page)
        top_row.addWidget(self.page_label, 1, QtCore.Qt.AlignCenter)
        top_row.addWidget(next_page)
        right.addLayout(top_row)

        self.stack = QtWidgets.QStackedWidget()
        for page in PAGES:
            page_widget = QtWidgets.QWidget()
            grid = QtWidgets.QGridLayout(page_widget)
            for i, (en, cn) in enumerate(page):
                btn = QtWidgets.QPushButton()
                btn.setObjectName("typeBtn")
                btn.setProperty("selected", False)
                btn.setProperty("negative", en in NEGATIVE_SET)
                btn.clicked.connect(lambda _checked, e=en: self.select_en(e))
                self.type_buttons[en] = btn
                r, c = divmod(i, 3)
                grid.addWidget(btn, r, c)
            grid.setAlignment(QtCore.Qt.AlignTop)
            self.stack.addWidget(page_widget)
        right.addWidget(self.stack, 1)

        for en in self.type_buttons:
            self.refresh_button(en)

        root.addLayout(left, 5)
        root.addLayout(right, 4)
        self.setCentralWidget(central)

        # 底部警告条
        warn = QtWidgets.QFrame()
        warn.setObjectName("warnBar")
        wl = QtWidgets.QHBoxLayout(warn)
        wl.setContentsMargins(10, 6, 10, 6)
        wt = QtWidgets.QLabel("本轮补采（按 2026-10-31 补采清单）：P0 老类（抱抱/击掌/跟随/光翼/聚焦/打坐/互火/牵手/熊抱/"
                               "谢幕/花式击掌/摸头/打闹/共舞）+ 新类 + 困难负样本；按钮上的 N/目标 是进度。"
                               "注意：无翼号不要真捡光翼（不可逆）。")
        wt.setObjectName("warnText")
        wl.addWidget(wt)
        backend_lbl = QtWidgets.QLabel("后端：%s" % self.backend)
        backend_lbl.setStyleSheet("color:#555; font-size:12px;")
        wl.addWidget(backend_lbl)
        quit_btn = QtWidgets.QPushButton("退出（F12）")
        quit_btn.setObjectName("pageBtn")
        quit_btn.clicked.connect(self.close)
        wl.addWidget(quit_btn)
        left.addWidget(warn)

    def set_always_on_top(self, on):
        flag = QtCore.Qt.WindowStaysOnTopHint if on else QtCore.Qt.Widget
        self.setWindowFlags(QtCore.Qt.Window | flag)
        self.show()

    # ── 预览 ─────────────────────────────────────────────────────────
    def update_preview(self):
        img = self.cap.get_latest()
        if img is None:
            return
        h, w = img.shape[:2]
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        qimg = QtGui.QImage(rgb.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
        pix = QtGui.QPixmap.fromImage(qimg)
        scaled = pix.scaled(self.preview_label.size(), QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    # ── 键盘轮询 + 连拍 ───────────────────────────────────────────────
    def poll(self):
        while True:
            try:
                vk = self.events.get_nowait()
            except queue.Empty:
                break
            self.handle_key(vk)
        if self.burst and (time.time() - self.last_burst) >= BURST_INTERVAL:
            self.do_capture(manual=False)
            self.last_burst = time.time()

    def handle_key(self, vk):
        if vk in NUMPAD_DIGIT:
            page = self.stack.currentIndex()
            n = NUMPAD_DIGIT[vk]
            if n < len(PAGES[page]):
                self.select_cur(self.page_start[page] + n)
        elif vk == VK_NUMPAD0:
            self.select_en(NEGATIVE_EN)
        elif vk == VK_F6:
            self.flip_page(1)
        elif vk == VK_F8:
            self.select_cur(self.cur + 1)
        elif vk == VK_F7:
            self.select_cur(self.cur - 1)
        elif vk == VK_F9:
            self.do_capture(manual=True)
        elif vk == VK_F10:
            self.toggle_burst()
        elif vk == VK_F12:
            self.close()

    def flip_page(self, delta):
        page = (self.stack.currentIndex() + delta) % len(PAGES)
        self.select_cur(self.page_start[page])

    # ── 选择 / 高亮 ──────────────────────────────────────────────────
    def select_en(self, en):
        for i, (e, _cn) in enumerate(FLAT):
            if e == en:
                self.select_cur(i)
                return

    def select_cur(self, global_idx):
        self.cur = global_idx % len(FLAT)
        self.refresh_selection()
        self.refresh_status()
        en, cn = FLAT[self.cur]
        print("  切换类型 -> %s (%s)" % (en, cn))
        beep(650 + (self.cur % 9) * 50, 40)

    def refresh_selection(self):
        page = 0
        for pi, start in enumerate(self.page_start):
            if start <= self.cur < start + len(PAGES[pi]):
                page = pi
                break
        self.stack.setCurrentIndex(page)
        self.page_label.setText("第 %d / %d 页 · %s"
                                % (page + 1, len(PAGES), PAGE_TITLES[page]))
        for en, btn in self.type_buttons.items():
            idx = next(i for i, (e, _) in enumerate(FLAT) if e == en)
            btn.setProperty("selected", idx == self.cur)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def refresh_button(self, en):
        btn = self.type_buttons.get(en)
        if btn is None:
            return
        cn = EN_TO_CN[en]
        target = TARGETS.get(en)
        if target:
            btn.setText("%s\n%s\n%d/%d 张" % (cn, en, self.counts[en], target))
        else:
            btn.setText("%s\n%s\n%d 张" % (cn, en, self.counts[en]))

    def refresh_status(self):
        en, cn = FLAT[self.cur]
        target = TARGETS.get(en)
        if target:
            left = max(0, target - self.counts[en])
            tgt = (" / 目标 %d（%s）" % (target, "已达标 ✔" if left == 0 else "还差 %d" % left))
        else:
            tgt = ""
        hint = HINTS.get(en, "")
        self.status_text.setText(
            "当前：<b>%s（%s）</b>　本轮已采 <b>%d</b> 张%s%s"
            % (cn, en, self.counts[en], tgt,
               ("<br><span style='color:#7a4b00'>%s</span>" % hint) if hint else ""))
        try:
            user32.SetConsoleTitleW("采集 [%s/%s] 连拍=%s"
                                    % (en, cn, "开" if self.burst else "关"))
        except Exception:
            pass

    # ── 截图保存 ─────────────────────────────────────────────────────
    def do_capture(self, manual=True):
        img = self.cap.get_latest()
        en, cn = FLAT[self.cur]
        if img is None:
            beep(220, 120)
            print("  暂时抓不到画面，稍等再按。")
            return
        gs = _gray_small(img)
        diff = -1.0 if self.last_saved_small is None else float(np.abs(gs - self.last_saved_small).mean())
        if self.last_saved_small is not None and diff < FREEZE_DIFF:
            self.dup_streak += 1
            beep(300, 60)
            print("  [%s/%s] 画面没变化（%.2f），已跳过——换个位置/角度再按" % (en, cn, diff))
            if self.dup_streak % 3 == 0:
                print("  ★ 明明在移动却连续提示没变化，说明后端抓不到实时画面，"
                      "请把游戏改成「无边框窗口」！")
            return
        d = os.path.join(SAVE_ROOT, en)
        os.makedirs(d, exist_ok=True)
        self.counts[en] += 1
        ts = datetime.now().strftime("%H%M%S")
        fn = os.path.join(d, "%s_%04d.png" % (ts, self.counts[en]))
        # 必须用 imencode + open(wb)：cv2.imwrite 在 Windows 中文路径下会静默失败
        ok, encoded = cv2.imencode('.png', img)
        if ok:
            try:
                with open(fn, 'wb') as f:
                    f.write(encoded.tobytes())
            except Exception:
                ok = False
        if not ok or not os.path.exists(fn):
            self.counts[en] -= 1
            beep(220, 160)
            print("  [保存失败] %s" % fn)
            return
        self.last_saved_small = gs
        self.dup_streak = 0
        self.refresh_button(en)
        self.refresh_status()
        tgt = TARGETS.get(en)
        if tgt and self.counts[en] >= tgt and en not in self._hit:
            self._hit.add(en)
            beep(1500, 100)
            time.sleep(0.12)
            beep(1900, 120)
            print("  ✔ [%s/%s] 已达本轮目标 %d 张！可以换下一类了（F8 下一类 / F6 翻页）"
                  % (en, cn, tgt))
        if manual:
            beep(1200, 40)
            print("  [%s/%s] 第 %d 张（画面变化 %.1f）" % (en, cn, self.counts[en], diff))
        else:
            beep(850, 25)
            print("  [连拍 %s/%s] 第 %d 张（变化 %.1f）" % (en, cn, self.counts[en], diff))

    def toggle_burst(self):
        self.burst = not self.burst
        if self.burst:
            self.last_burst = time.time()
        self.burst_btn.setText("连拍：%s（F10）" % ("开" if self.burst else "关"))
        beep(900 if self.burst else 400, 80)
        print("  连拍：%s（期间务必走动/转镜头）" % ("开启" if self.burst else "关闭"))
        self.refresh_status()

    # ── 清理 ─────────────────────────────────────────────────────────
    def closeEvent(self, event):
        if not self._cleaned:
            self._cleaned = True
            self.preview_timer.stop()
            self.poll_timer.stop()
            self.cap.stop()
            print("\n=== 本轮采集统计（累计，含之前已采） ===")
            total = 0
            left = 0
            for en, cn in FLAT:
                n = self.counts[en]
                total += n
                tgt = TARGETS.get(en)
                if tgt:
                    left += max(0, tgt - n)
                    print("  %s (%s): %d/%d 张%s" % (en, cn, n, tgt,
                                                    "" if n < tgt else "  ✔达标"))
                elif n:
                    print("  %s (%s): %d 张" % (en, cn, n))
            print("  合计：%d 张；目标还差 %d 张；保存目录：%s" % (total, left, SAVE_ROOT))
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
