# -*- coding: utf-8 -*-
"""
互动接受图标采集器（低级键盘钩子 + DXGI 实时截图 + 冻结帧保险）

【重要】请用同目录的 run_collect.bat 启动（自动申请管理员权限）。

截图后端：优先 DXGI（bettercam/dxcam，OBS 游戏捕获同原理，全屏游戏也能实时抓帧），
不可用时才回退 mss/GDI（GDI 抓全屏游戏会冻结在同一帧，启动时会明确警告）。
内置"冻结/重复保险"：每次保存都会和上一张比，画面没变化就拒收并提示，
即使后端出问题或你原地连拍，也不会再存下一堆相同的废图。

切换要采的类型（游戏里按即可，不拦截游戏本身的按键）：
  小键盘 1=抱抱 2=背背 3=击掌 4=碰拳 5=牵手 0=负样本（需开 NumLock）
  没有小键盘的笔记本：F8 切到下一个类型，F7 切到上一个
  F9 = 截当前类型 1 张（画面没变化会被跳过）
  F10 = 开/关连拍（每 1.5s 一张，期间务必走动/转镜头换画面）
  F12 = 退出，并打印每类数量

每次操作都有提示音：截图成功"嘀"一声，画面没变化是低沉短音，方便全屏时确认。
截图为游戏窗口客户区，存到 D:\\光遇互动采集\\<类型>\\
（无 D 盘时存到桌面 sky_interaction_raw）
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

# (英文类名, 中文)  —— 前 6 个为旧类（小键盘 1-5/0 键位保持不变），后 7 个为 v5 新增
TYPES = [
    ("hug", "抱抱"),
    ("carry", "背背"),
    ("high_five", "击掌"),
    ("fist_bump", "碰拳"),
    ("hand", "牵手"),
    ("negative", "负样本"),
    ("open_tree", "好友树四角星"),
    ("follow", "跟随"),
    ("light", "举蜡烛点火"),
    ("focus", "眼睛聚焦"),
    ("seat", "打坐图标"),
    ("wing_light", "光翼!!只远截"),
    ("teleport", "传送确认"),
]
# 小键盘数字（开 NumLock）-> 类型索引：1-9 选前 9 类、0 选负样本；其余用 F7/F8 循环
NUMPAD_TO_IDX = {0x61: 0, 0x62: 1, 0x63: 2, 0x64: 3, 0x65: 4,
                0x60: 5, 0x66: 6, 0x67: 7, 0x68: 8, 0x69: 9}
VK_F7, VK_F8, VK_F9, VK_F10, VK_F12 = 0x76, 0x77, 0x78, 0x79, 0x7B

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
# bettercam/dxcam 的 grab(region=...) 用的是「所选输出(显示器)左上角为原点」的相对坐标，
# 不是绝对桌面坐标；单屏时输出原点即 (0,0)，双屏时必须先选屏再换算。

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
        r, b = l + reg["width"], t + reg["height"]
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


def main():
    region = find_game_window()
    if not region:
        print("[错误] 没找到“光·遇”游戏窗口，请先进入游戏再运行本脚本。")
        beep(300, 200)
        return

    grabber = ScreenGrabber()
    if grabber.backend == "DXGI":
        print("=" * 60)
        print(" 截图后端：DXGI（%s）— 全屏游戏实时抓帧，OK" % grabber.mod_name)
        print("=" * 60)
    else:
        print("=" * 60)
        print(" 截图后端：mss/GDI（未检测到 bettercam）")
        print(" 警告：GDI 抓【全屏】游戏可能冻结在同一帧！")
        print(" 建议：把游戏设置成「无边框窗口/窗口」模式，或安装 bettercam")
        print(" （pip install bettercam）。本程序的冻结保险会拒收重复帧。")
        print("=" * 60)

    cap = CaptureThread(grabber)
    cap.start()
    # 等待首帧
    t0 = time.time()
    while cap.get_latest() is None and time.time() - t0 < 4:
        time.sleep(0.1)
    if cap.get_latest() is None:
        print("[错误] 启动后一直抓不到画面，请确认游戏没有最小化，然后重试。")
        beep(220, 300)
        return

    counts = {en: 0 for en, _ in TYPES}
    cur = 0
    burst = False
    dup_streak = 0
    last_saved_small = None
    events = queue.Queue()
    _t, _cb = start_keyboard_hook(events)
    time.sleep(0.15)
    if not events.empty() and events.queue[0] == -1:
        print("[错误] 键盘钩子安装失败，请用 run_collect.bat（管理员）启动。")
        return

    print("光遇互动图标采集器已就绪（v5 类别）")
    print(" 小键盘 1-9 选类别 / 0 负样本（开NumLock）；F7/F8 前后循环切换")
    print(" F9 截图   F10 连拍（要走动换画面）   F12 退出")
    print(" 保存目录：%s" % SAVE_ROOT)
    print(" 类别清单：")
    for i, (en, cn) in enumerate(TYPES):
        key = "小键盘%d" % (0 if i == 5 else i + 1) if i <= 9 else "F7/F8"
        print("   [%s] %s (%s)" % (key, en, cn))
    print(" ★★ wing_light 光翼：只准远距离截图，绝不靠近、绝不按 F，无翼小号捡了不可逆！")
    print("-" * 60)

    def refresh_title():
        en, cn = TYPES[cur]
        seg = " ".join("%s=%d" % (e, counts[e]) for e, _ in TYPES)
        try:
            user32.SetConsoleTitleW("采集 [%s/%s] %s 连拍=%s"
                                   % (en, cn, seg, "开" if burst else "关"))
        except Exception:
            pass

    def save_shot():
        """返回 (status, en, cn, n, diff, fn)：status=ok/dup/no_frame/save_fail。"""
        nonlocal last_saved_small
        img = cap.get_latest()
        if img is None:
            en, cn = TYPES[cur]
            return "no_frame", en, cn, 0, -1.0, ""
        gs = _gray_small(img)
        diff = -1.0 if last_saved_small is None else float(np.abs(gs - last_saved_small).mean())
        if last_saved_small is not None and diff < FREEZE_DIFF:
            en, cn = TYPES[cur]
            return "dup", en, cn, 0, diff, ""
        en, cn = TYPES[cur]
        d = os.path.join(SAVE_ROOT, en)
        os.makedirs(d, exist_ok=True)
        counts[en] += 1
        ts = datetime.now().strftime("%H%M%S")
        fn = os.path.join(d, "%s_%04d.png" % (ts, counts[en]))
        # 必须用 imencode + open(wb)：cv2.imwrite 在 Windows 中文路径下会静默失败
        ok, encoded = cv2.imencode('.png', img)
        if ok:
            try:
                with open(fn, 'wb') as f:
                    f.write(encoded.tobytes())
            except Exception:
                ok = False
        if not ok or not os.path.exists(fn):
            counts[en] -= 1
            return "save_fail", en, cn, 0, diff, fn
        last_saved_small = gs
        return "ok", en, cn, counts[en], diff, fn

    last_burst = 0.0
    running = True
    refresh_title()
    while running:
        try:
            vk = events.get(timeout=0.05)
        except queue.Empty:
            vk = None

        if vk is not None:
            if vk in NUMPAD_TO_IDX:
                cur = NUMPAD_TO_IDX[vk]
                dup_streak = 0
                beep(650 + cur * 60, 50)
                print("  切换类型 -> %s (%s)" % (TYPES[cur][0], TYPES[cur][1]))
            elif vk == VK_F8:
                cur = (cur + 1) % len(TYPES)
                dup_streak = 0
                beep(650 + cur * 60, 50)
                print("  切换类型 -> %s (%s)" % (TYPES[cur][0], TYPES[cur][1]))
            elif vk == VK_F7:
                cur = (cur - 1) % len(TYPES)
                dup_streak = 0
                beep(650 + cur * 60, 50)
                print("  切换类型 -> %s (%s)" % (TYPES[cur][0], TYPES[cur][1]))
            elif vk == VK_F9:
                status, en, cn, n, diff, fn = save_shot()
                if status == "ok":
                    dup_streak = 0
                    beep(1200, 40)
                    print("  [%s/%s] 第 %d 张（画面变化 %.1f）" % (en, cn, n, diff))
                elif status == "dup":
                    dup_streak += 1
                    beep(300, 70)
                    print("  [%s/%s] 画面没变化（%.2f），已跳过——换个位置/角度再按" % (en, cn, diff))
                    if dup_streak > 0 and dup_streak % 3 == 0:
                        print("  ★ 如果你明明在移动画面却连续提示没变化，说明截图后端抓不到实时画面，"
                              "请把游戏改成「无边框窗口」后告诉我！")
                elif status == "no_frame":
                    beep(220, 150)
                    print("  暂时抓不到画面，稍等再按。")
                else:
                    beep(220, 200)
                    print("  [保存失败] %s" % fn)
            elif vk == VK_F10:
                burst = not burst
                beep(900 if burst else 400, 80)
                print("  连拍：%s（期间务必走动/转镜头）" % ("开启" if burst else "关闭"))
            elif vk == VK_F12:
                beep(400, 120)
                running = False
            refresh_title()

        if burst and (time.time() - last_burst) >= BURST_INTERVAL:
            status, en, cn, n, diff, fn = save_shot()
            if status == "ok":
                dup_streak = 0
                beep(850, 25)
                print("  [连拍 %s/%s] 第 %d 张（变化 %.1f）" % (en, cn, n, diff))
            elif status == "dup":
                dup_streak += 1
                beep(280, 40)
                print("  [连拍] 画面没变化，跳过本张")
            elif status == "no_frame":
                print("  [连拍] 暂未抓到画面。")
            else:
                beep(220, 200)
                print("  [连拍保存失败] %s" % fn)
            last_burst = time.time()
            refresh_title()

    cap.stop()
    print("\n=== 本次采集统计 ===")
    for en, cn in TYPES:
        print("  %s (%s): %d 张" % (en, cn, counts[en]))
    print("保存目录：%s" % SAVE_ROOT)


if __name__ == "__main__":
    main()
