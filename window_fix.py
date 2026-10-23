# -*- coding: utf-8 -*-
"""把光遇窗口的「客户区」调成 1920x1080 —— 模板素材的标定基准。

为什么需要：
    panel_detector 的模板按 ref_height=1080 截取，运行时按帧高缩放
    （panel_detector.py 顶部注释、doctor.py 的窗口检查都以此为准）。
    窗口越偏离 1920x1080，缩放插值带来的偏差越大，ROI 与模板分会一起变差。
    手动在游戏里改画质/全屏很麻烦，这个脚本直接把窗口改成 1080p。
    参考：SkyAuto 的「设为 1920×1080」按钮 + DataContext.ScaleTo1080PRatio
    （2026-10-06 DSH 从其 i18n/类型名还原，见 PROJECT_HANDOFF.md 第二十九章）。

用法：
    py window_fix.py                  # 只报告当前尺寸与换算，*不动*你的窗口
    py window_fix.py --apply          # 真的把客户区改成 1920x1080（保持左上角位置）
    py window_fix.py --apply --x 0 --y 0
    py window_fix.py --size 1600x900  # 换一个目标尺寸

四个必须注意的点：
  1. 必须先 SetProcessDPIAware()。否则 GetClientRect/SetWindowPos 拿到的是
     「逻辑像素」，在 125%/150% 缩放的显示器上会与实际像素错开，算出来的尺寸是错的。
  2. 改的是「客户区」。SetWindowPos 设的是整窗（含标题栏与边框），所以必须先用
     AdjustWindowRectExForDpi 把 1920x1080 反算成整窗尺寸，否则客户区会少掉边框。
  3. 改窗口尺寸 != 改游戏内部渲染分辨率。光遇 PC 按客户区尺寸渲染，两者一致；
     但如果你在游戏里单独调低过画质/渲染分辨率，模板匹配仍然会偏。
     另外独占全屏（exclusive fullscreen）改不了尺寸，先在游戏里切成窗口或无边框窗口。
  4. **权限**：2026-10-06 真机实测，游戏在跑时三种改尺寸写法全部失败。
     光遇国服客户端带反作弊、通常以管理员权限运行，而普通终端里的 Python 权限比它低，
     Windows 的 UIPI 会拦掉对高权限窗口的一切「写」操作——读窗口位置/尺寸不受影响，
     所以 GetClientRect 一直是好的，只有 SetWindowPos/MoveWindow 会失败。
     **对策：把终端「以管理员身份运行」，再执行 --apply。**
     失败时本脚本会逐个策略打印 Win32 错误码与含义，别只看一句"失败"。

只在本文件里做 Windows API 调用；纯计算部分（compute_outer_size / scale_ratio /
needs_fix / parse_size / describe / explain_win32_error）不碰 Win32，可离线测试
（test_window_fix.py）。
"""
import argparse
import ctypes
import sys

# 模板素材的标定基准
REF_W, REF_H = 1920, 1080

# 与 doctor.py 保持一致的游戏窗口标题（子串匹配只允许长标题，避免误中 Skype）
WINDOW_TITLES = ("光·遇", "Sky: Children of the Light", "光遇", "Sky")
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADING_HOSTING_WINDOW_CLASS",
                   "CASCADIA_HOSTING_WINDOW_CLASS"}

GWL_STYLE, GWL_EXSTYLE = -16, -20
SW_RESTORE = 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010

# SetWindowPos/MoveWindow 失败时最常见的几个 Win32 错误码（纯查表，可离线测）
WIN32_ERRORS = {
    5: "拒绝访问 —— 游戏进程权限比你高（反作弊/以管理员运行），"
       "请把终端「以管理员身份运行」后重试",
    6: "无效句柄",
    87: "参数错误 —— 尺寸或样式不被该窗口接受",
    1400: "无效的窗口句柄 —— 窗口可能在两次调用之间被销毁或重建了",
    1401: "无效的菜单句柄",
}


# ───────────────────────── 纯计算（可离线测试） ─────────────────────────

def scale_ratio(client_h, ref_h=REF_H):
    """素材缩放比 = 实际客户区高度 / 标定高度（1080p 时为 1.0）。"""
    return float(client_h) / float(ref_h)


def needs_fix(client_w, client_h, target_w=REF_W, target_h=REF_H):
    """客户区是否已经不是目标尺寸。"""
    return int(client_w) != int(target_w) or int(client_h) != int(target_h)


def compute_outer_size(target_w, target_h, adjust):
    """把目标「客户区」尺寸反算成「整窗」尺寸。

    adjust 是注入的矩形调整函数：吃 {"left","top","right","bottom"}，
    吐加上标题栏/边框后的同一个 dict。真实实现传 AdjustWindowRectExForDpi，
    测试里传一个假函数，这样这段逻辑不需要 Windows 也能验证。
    """
    rect = adjust({"left": 0, "top": 0, "right": int(target_w), "bottom": int(target_h)})
    return int(rect["right"] - rect["left"]), int(rect["bottom"] - rect["top"])


def describe(client_w, client_h, target_w=REF_W, target_h=REF_H):
    """一行人类可读的现状说明（确定性，便于测试与写日志）。"""
    ratio = scale_ratio(client_h)
    if needs_fix(client_w, client_h, target_w, target_h):
        return ("客户区 %dx%d，不是标定的 %dx%d；素材缩放 %.3f"
                % (client_w, client_h, target_w, target_h, ratio))
    return "客户区 %dx%d，正好是标定尺寸（素材缩放 %.2f）" % (client_w, client_h, ratio)


def explain_win32_error(code):
    """把 Win32 错误码翻译成中文说明（未知码也给出可读输出）。"""
    code = int(code)
    if code == 0:
        return "Win32 错误 0：取不到错误码（可能被后续 API 调用冲掉了 last-error）"
    if code in WIN32_ERRORS:
        return "Win32 错误 %d：%s" % (code, WIN32_ERRORS[code])
    return "Win32 错误 %d：未收录，可用 `certutil -error %d` 查" % (code, code)


def parse_size(text):
    """'1920x1080' / '1920X1080' / '1920*1080' -> (1920, 1080)。非法抛 ValueError。"""
    s = str(text).lower().replace("*", "x").replace(",", "x")
    parts = [p for p in s.split("x") if p.strip()]
    if len(parts) != 2:
        raise ValueError("尺寸格式应为 宽x高，例如 1920x1080，收到 %r" % text)
    w, h = int(parts[0]), int(parts[1])
    if w <= 0 or h <= 0:
        raise ValueError("尺寸必须为正数，收到 %r" % text)
    return w, h


# 虚拟屏幕（覆盖全部显示器）的度量常量
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def offscreen_px(client_left, client_top, client_w, client_h, screen):
    """客户区有多少像素落在虚拟屏幕之外，返回 (横向超出, 纵向超出)；都 0 表示完整可见。

    screen 是 (x, y, w, h)。为什么要查这个：
      * 客户区哪怕超出屏幕一点点，那部分 Windows 根本不会绘制，截到的就是桌面/黑块，
        落在里面的模板与 ROI 必然判错；
      * DXGI 截屏（bettercam/dxcam）对越界/跨屏区域更容易直接抓取失败，
        于是 panel_detector 连续失败 3 次后会永久退回 GDI（见其 1321 行注释），
        等于把"零干扰截屏"这个优化白白丢掉。
      2026-10-06 真机就出现过 @(231,92) + 1920x1080 这种右边/下边出屏的位置。
    """
    vx, vy, vw, vh = [int(v) for v in screen]
    l, t = int(client_left), int(client_top)
    r, b = l + int(client_w), t + int(client_h)
    dx = max(0, vx - l) + max(0, r - (vx + vw))
    dy = max(0, vy - t) + max(0, b - (vy + vh))
    return dx, dy


def fully_visible(client_left, client_top, client_w, client_h, screen):
    """客户区是否完整落在虚拟屏幕内。"""
    return offscreen_px(client_left, client_top, client_w, client_h, screen) == (0, 0)


# ───────────────────────── Win32（只在 Windows 上调用） ─────────────────────────

_USER32 = None


def _user32():
    """use_last_error=True 才能可靠拿到 SetWindowPos 的失败原因；
    并显式声明签名，避免 64 位下 HWND / 尺寸被按错误的宽度传递。"""
    global _USER32
    if _USER32 is None:
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]
        u.SetWindowPos.restype = wintypes.BOOL
        u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.BOOL]
        u.MoveWindow.restype = wintypes.BOOL
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.restype = wintypes.BOOL
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.IsZoomed.restype = wintypes.BOOL
        _USER32 = u
    return _USER32


def make_dpi_aware():
    """拿物理像素。必须在任何 GetClientRect / SetWindowPos 之前调用。"""
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def is_elevated():
    """当前进程是否以管理员权限运行（失败时返回 None = 未知）。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def find_game_window(titles=WINDOW_TITLES, console_classes=CONSOLE_CLASSES):
    """枚举顶层窗口找光遇，返回 dict（含 hwnd / title / 客户区 / 整窗 / dpi），找不到 None。"""
    from ctypes import wintypes
    user32 = _user32()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if not title:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in console_classes:
            return True
        for pri, t in enumerate(titles):
            if title == t:
                found.append((pri, hwnd, title))
                break
            if len(t) >= 8 and t in title:
                found.append((pri + 100, hwnd, title))
                break
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        return None
    found.sort(key=lambda c: c[0])
    _, hwnd, title = found[0]

    crect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(crect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    orect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(orect))
    try:
        dpi = int(user32.GetDpiForWindow(hwnd)) or 96
    except Exception:
        dpi = 96
    sm = user32.GetSystemMetrics
    screen = (int(sm(SM_XVIRTUALSCREEN)), int(sm(SM_YVIRTUALSCREEN)),
              int(sm(SM_CXVIRTUALSCREEN)), int(sm(SM_CYVIRTUALSCREEN)))
    return {
        "hwnd": hwnd, "title": title,
        "client_w": int(crect.right - crect.left),
        "client_h": int(crect.bottom - crect.top),
        "left": int(origin.x), "top": int(origin.y),
        "outer_left": int(orect.left), "outer_top": int(orect.top),
        "outer_w": int(orect.right - orect.left),
        "outer_h": int(orect.bottom - orect.top),
        "dpi": dpi,
        "maximized": bool(user32.IsZoomed(hwnd)),
        "screen": screen,
    }


def _make_adjust(hwnd):
    """返回 AdjustWindowRectExForDpi 的包装（优先带 DPI 的版本，回退旧 API）。"""
    from ctypes import wintypes
    user32 = _user32()
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    try:
        dpi = int(user32.GetDpiForWindow(hwnd)) or 96
    except Exception:
        dpi = 96
    has_dpi_api = hasattr(user32, "AdjustWindowRectExForDpi")

    def adjust(rect):
        r = wintypes.RECT(rect["left"], rect["top"], rect["right"], rect["bottom"])
        if has_dpi_api:
            ok = user32.AdjustWindowRectExForDpi(ctypes.byref(r), style, False, exstyle, dpi)
        else:
            ok = user32.AdjustWindowRectEx(ctypes.byref(r), style, False, exstyle)
        if not ok:
            raise OSError("AdjustWindowRect%s 失败（%s）"
                          % ("ExForDpi" if has_dpi_api else "Ex",
                             explain_win32_error(ctypes.get_last_error())))
        return {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}

    return adjust, has_dpi_api, dpi


def apply_resize(hwnd, outer_w, outer_h, x, y):
    """把整窗尺寸/位置设成给定值；最大化先还原。

    返回 (是否成功, 成功的策略名, 最后一次的 Win32 错误码)。
    依次尝试三种写法，因为不同窗口/权限状态下能生效的那个不一样：
      A. SetWindowPos + SWP_NOZORDER + SWP_NOACTIVATE（最不打扰用户）
      B. SetWindowPos 不带 SWP_NOACTIVATE（有些窗口不接受"不激活"的改尺寸）
      C. MoveWindow（老 API，行为略有不同）
    """
    user32 = _user32()
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    attempts = [
        ("SetWindowPos(不激活不置顶)",
         lambda: user32.SetWindowPos(hwnd, None, int(x), int(y), int(outer_w), int(outer_h),
                                     SWP_NOZORDER | SWP_NOACTIVATE)),
        ("SetWindowPos(允许激活)",
         lambda: user32.SetWindowPos(hwnd, None, int(x), int(y), int(outer_w), int(outer_h),
                                     SWP_NOZORDER)),
        ("MoveWindow",
         lambda: user32.MoveWindow(hwnd, int(x), int(y), int(outer_w), int(outer_h), True)),
    ]
    last_err = 0
    for name, call in attempts:
        ctypes.set_last_error(0)
        ok = bool(call())
        err = ctypes.get_last_error()
        print("      尝试 %-24s -> %s%s"
              % (name, "成功" if ok else "失败",
                 "" if ok else "（%s）" % explain_win32_error(err)))
        if ok:
            return True, name, err
        last_err = err
    return False, "", last_err


def main(argv=None):
    ap = argparse.ArgumentParser(description="把光遇窗口客户区调成 1920x1080")
    ap.add_argument("--apply", action="store_true", help="真的修改窗口（默认只报告）")
    ap.add_argument("--size", default="%dx%d" % (REF_W, REF_H), help="目标客户区尺寸，默认 1920x1080")
    ap.add_argument("--x", type=int, default=None, help="窗口左上角 X，默认保持原位")
    ap.add_argument("--y", type=int, default=None, help="窗口左上角 Y，默认保持原位")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("win"):
        print("本脚本只支持 Windows（光遇 PC 版）")
        return 1

    try:
        target_w, target_h = parse_size(args.size)
    except ValueError as e:
        print("参数错误：%s" % e)
        return 1

    make_dpi_aware()   # 必须在量窗口之前

    win = find_game_window()
    if not win:
        print("没找到光遇窗口 —— 先把游戏开起来（窗口标题要含 '光·遇'，不能最小化）")
        return 1

    elev = is_elevated()
    print("找到游戏窗口「%s」" % win["title"])
    print("  本进程权限: %s"
          % ("管理员" if elev else ("普通用户（改不了高权限窗口；--apply 失败就先提权重试）"
                                    if elev is not None else "未知")))
    print("  DPI: %d（缩放 %.0f%%）" % (win["dpi"], win["dpi"] / 96.0 * 100))
    print("  客户区: %dx%d @(%d,%d)" % (win["client_w"], win["client_h"],
                                        win["left"], win["top"]))
    print("  整窗:   %dx%d" % (win["outer_w"], win["outer_h"]))
    print("  %s" % describe(win["client_w"], win["client_h"], target_w, target_h))
    scr = win.get("screen") or (0, 0, 0, 0)
    print("  虚拟屏幕（全部显示器）: %dx%d @(%d,%d)" % (scr[2], scr[3], scr[0], scr[1]))
    dx, dy = offscreen_px(win["left"], win["top"], win["client_w"], win["client_h"], scr)
    if (dx, dy) != (0, 0):
        print("  [警告] 客户区有 %d 像素横向、%d 像素纵向落在屏幕之外 —— "
              "那部分截到的会是桌面/黑块，落在里面的模板与 ROI 必然判错；"
              "而且 DXGI 截屏（bettercam/dxcam）对越界区域容易直接失败、"
              "连续 3 次后会被动退回 GDI。" % (dx, dy))
        print("         建议把窗口完整挪进某一块屏（例如铺满左侧那块 1920x1080 的屏）。")
    else:
        print("  客户区完整落在屏幕内（可见，未被切边）")
    if win["maximized"]:
        print("  注意：窗口当前是最大化，--apply 时会先还原（ShowWindow SW_RESTORE）")

    try:
        adjust, has_dpi_api, _ = _make_adjust(win["hwnd"])
        outer_w, outer_h = compute_outer_size(target_w, target_h, adjust)
    except Exception as e:
        print("算不出整窗尺寸：%s" % e)
        return 1
    print("  目标客户区 %dx%d 对应整窗 %dx%d（%s）"
          % (target_w, target_h, outer_w, outer_h,
             "AdjustWindowRectExForDpi" if has_dpi_api else "AdjustWindowRectEx（旧 API，未按 DPI 校正）"))

    if not needs_fix(win["client_w"], win["client_h"], target_w, target_h) \
            and args.x is None and args.y is None:
        print("已经是目标尺寸，无需修改。")
        return 0

    x = win["outer_left"] if args.x is None else args.x
    y = win["outer_top"] if args.y is None else args.y

    if not args.apply:
        print("")
        print("（预演，未改动你的窗口）要真正生效请加 --apply：")
        print("    py window_fix.py --apply")
        print("  提示：若 --apply 报「拒绝访问」，请把终端「以管理员身份运行」后重试。")
        return 0

    print("")
    ok, how, err = apply_resize(win["hwnd"], outer_w, outer_h, x, y)
    if not ok:
        print("")
        print("三种改尺寸的写法都失败了。最后一次：%s" % explain_win32_error(err))
        if err == 5:
            print("  -> 这是权限问题（UIPI）。关掉终端，改用「以管理员身份运行」打开，再执行：")
            print("         py window_fix.py --apply")
            print("     仍然失败的话，就在游戏里手动把窗口拖到 1920x1080（或设成无边框窗口再拖）。")
        elif err == 87:
            print("  -> 游戏不接受这个尺寸。试试在游戏里切成「窗口」或「无边框窗口」模式再跑。")
        else:
            print("  -> 若游戏是独占全屏，先在游戏里切成窗口/无边框窗口；仍不行请把上面输出发我。")
        return 1

    after = find_game_window()
    if not after:
        print("改完之后找不到窗口了，请手动确认")
        return 1
    print("改完（策略：%s）：客户区 %dx%d，整窗 %dx%d（素材缩放 %.3f）"
          % (how, after["client_w"], after["client_h"], after["outer_w"], after["outer_h"],
             scale_ratio(after["client_h"])))
    if needs_fix(after["client_w"], after["client_h"], target_w, target_h):
        print("没达到目标尺寸 —— 游戏可能有自己的窗口尺寸约束，或仍在独占全屏")
        return 1
    print("已对齐模板标定基准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
