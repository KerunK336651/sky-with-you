# -*- coding: utf-8 -*-
"""
光遇互动图标采集器 · GUI 版（PyQt5 + 低级键盘钩子 + DXGI 实时截图 + 冻结帧保险）

【重要】请用同目录的 run_collect_gui.bat 启动（自动申请管理员权限）。

和命令行版 collect_interactions.py 功能一致，但有图形界面：
  - 左侧实时预览：直接看到当前抓到的游戏画面，确认要截的内容；
  - 右侧分类别（4 页）：每个按钮显示「中文名 + 英文类名 + 已采张数」，选中高亮，
    鼠标可点，也可以继续用键盘（游戏全屏时只能用键盘 + 提示音）。

切换 / 截图按键（游戏里按即可，不拦截游戏本身的按键）：
  小键盘 1-9 = 选当前页里的第 N 个类；小键盘 0 = 快速切「通用负样本」（需开 NumLock）
  F6 = 翻到下一页；F8 = 下一个类（跨页）；F7 = 上一个类
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
# 第 1、2 页是双人互动邀请（含 v6 新增 10 个动作 + 互火）；
# 第 3 页是界面/功能图标；第 4 页是负样本（标注时不画框，作为困难背景）。
PAGES = [
    [  # 第 1 页 · 双人互动（上）
        ("hug", "抱抱"),
        ("carry", "背背"),
        ("high_five", "击掌"),
        ("fist_bump", "碰拳"),
        ("hand", "牵手"),
        ("head_pat", "摸摸头"),
        ("play_fight", "打闹"),
        ("bear_hug", "熊抱"),
        ("princess_carry", "公主抱"),
    ],
    [  # 第 2 页 · 双人互动（下）
        ("spin_dance", "双人旋转共舞"),
        ("shoulder", "搭肩"),
        ("curtain_call", "谢幕"),
        ("whisper", "耳语"),
        ("fancy_high_five", "默契花式击掌"),
        ("handshake", "握手"),
        ("mutual_light", "互火·点亮"),
    ],
    [  # 第 3 页 · 界面/功能图标
        ("chat_input", "聊天框"),
        ("open_tree", "好友树四角星"),
        ("follow", "跟随"),
        ("light", "举蜡烛点火"),
        ("focus", "眼睛聚焦"),
        ("seat", "打坐图标"),
        ("wing_light", "光翼·只远截"),
        ("teleport", "传送确认"),
    ],
    [  # 第 4 页 · 负样本（标注时都是背景）
        ("negative", "通用负样本"),
        ("replay", "留影·负样本"),
        ("paper_boat", "纸船·负样本"),
        ("music_note", "音符·负样本"),
        ("outfit", "换装·负样本"),
    ],
]
FLAT = [t for _page in PAGES for t in _page]
EN_TO_CN = {en: cn for en, cn in FLAT}
NEGATIVE_EN = "negative"
# 每个类标记用途，用于按钮视觉区分
NEGATIVE_SET = {"negative", "replay", "paper_boat", "music_note", "outfit"}

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

        self.counts = {en: 0 for en, _ in FLAT}
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
        wt = QtWidgets.QLabel("⚠ wing_light（光翼）只准远距离截图，绝不靠近、绝不按 F，无翼小号捡了不可逆！")
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
        self.page_label.setText("第 %d / %d 页" % (page + 1, len(PAGES)))
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
        btn.setText("%s\n%s\n%d 张" % (cn, en, self.counts[en]))

    def refresh_status(self):
        en, cn = FLAT[self.cur]
        self.status_text.setText("当前：<b>%s（%s）</b>　本类已采 <b>%d</b> 张"
                                 % (cn, en, self.counts[en]))
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
            print("\n=== 本次采集统计 ===")
            total = 0
            for en, cn in FLAT:
                n = self.counts[en]
                total += n
                if n:
                    print("  %s (%s): %d 张" % (en, cn, n))
            print("  合计：%d 张；保存目录：%s" % (total, SAVE_ROOT))
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
