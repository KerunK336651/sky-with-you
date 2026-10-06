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

三个必须注意的点：
  1. 必须先 SetProcessDPIAware()。否则 GetClientRect/SetWindowPos 拿到的是
     「逻辑像素」，在 125%/150% 缩放的显示器上会与实际像素错开，算出来的尺寸是错的。
  2. 改的是「客户区」。SetWindowPos 设的是整窗（含标题栏与边框），所以必须先用
     AdjustWindowRectExForDpi 把 1920x1080 反算成整窗尺寸，否则客户区会少掉边框。
  3. 改窗口尺寸 != 改游戏内部渲染分辨率。光遇 PC 按客户区尺寸渲染，两者一致；
     但如果你在游戏里单独调低过画质/渲染分辨率，模板匹配仍然会偏。
     另外独占全屏（exclusive fullscreen）改不了尺寸，先在游戏里切成窗口或无边框窗口。

只在本文件里做 Windows API 调用；纯计算部分（compute_outer_size / scale_ratio /
needs_fix）不碰 Win32，可离线测试（test_window_fix.py）。
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


# ───────────────────────── Win32（只在 Windows 上调用） ─────────────────────────

def _user32():
    return ctypes.windll.user32


def make_dpi_aware():
    """拿物理像素。必须在任何 GetClientRect / SetWindowPos 之前调用。"""
    try:
        _user32().SetProcessDPIAware()
        return True
    except Exception:
        return False


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
            raise OSError("AdjustWindowRect%s 失败" % ("ExForDpi" if has_dpi_api else "Ex"))
        return {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}

    return adjust, has_dpi_api, dpi


def apply_resize(hwnd, outer_w, outer_h, x, y):
    """把整窗尺寸/位置设成给定值；最大化状态先还原。返回是否 SetWindowPos 成功。"""
    user32 = _user32()
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetWindowPos(hwnd, 0, int(x), int(y), int(outer_w), int(outer_h),
                                    SWP_NOZORDER | SWP_NOACTIVATE))


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

    print("找到游戏窗口「%s」" % win["title"])
    print("  DPI: %d（缩放 %.0f%%）" % (win["dpi"], win["dpi"] / 96.0 * 100))
    print("  客户区: %dx%d @(%d,%d)" % (win["client_w"], win["client_h"],
                                        win["left"], win["top"]))
    print("  整窗:   %dx%d" % (win["outer_w"], win["outer_h"]))
    print("  %s" % describe(win["client_w"], win["client_h"], target_w, target_h))
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
        return 0

    if not apply_resize(win["hwnd"], outer_w, outer_h, x, y):
        print("SetWindowPos 失败 —— 如果游戏是独占全屏，先在游戏里切成窗口/无边框窗口")
        return 1

    after = find_game_window()
    if not after:
        print("改完之后找不到窗口了，请手动确认")
        return 1
    print("")
    print("改完：客户区 %dx%d，整窗 %dx%d（素材缩放 %.3f）"
          % (after["client_w"], after["client_h"], after["outer_w"], after["outer_h"],
             scale_ratio(after["client_h"])))
    if needs_fix(after["client_w"], after["client_h"], target_w, target_h):
        print("没达到目标尺寸 —— 游戏可能有自己的窗口尺寸约束，或仍在独占全屏")
        return 1
    print("已对齐模板标定基准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
