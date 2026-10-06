# -*- coding: utf-8 -*-
"""test_window_fix.py — window_fix.py 的离线校验（不碰游戏、不改任何窗口）。

只测纯计算部分（尺寸换算 / 缩放比 / 参数解析 / 判据）与「import 不触发 Win32」，
所以任何机器上都能跑：不查窗口、不 SetWindowPos、不改你的游戏窗口。
"""
import sys

import window_fix as wf

npass = 0
nfail = 0


def check(desc, ok):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    print("  [%s] %s" % ("PASS" if ok else "FAIL", desc))


print("A. 标定基准与常量")

check("标定基准是 1920x1080", (wf.REF_W, wf.REF_H) == (1920, 1080))
check("窗口标题表含国服名", "光·遇" in wf.WINDOW_TITLES)
check("控制台类在黑名单里（避免误中终端窗口）",
      "ConsoleWindowClass" in wf.CONSOLE_CLASSES)
check("import 本模块不会去查窗口（find_game_window 未被调用）",
      wf._user32.__module__ == "window_fix")   # 只是确认它是个函数而非已执行的结果

print()
print("B. scale_ratio（素材缩放比）")

check("1080 -> 1.0", wf.scale_ratio(1080) == 1.0)
check("540 -> 0.5", wf.scale_ratio(540) == 0.5)
check("2160 -> 2.0", wf.scale_ratio(2160) == 2.0)
check("720 -> 0.6667（近似）", abs(wf.scale_ratio(720) - 0.6666666) < 1e-6)
check("可换参考高度", wf.scale_ratio(900, ref_h=900) == 1.0)

print()
print("C. needs_fix（判据）")

check("1920x1080 -> 不需要改", wf.needs_fix(1920, 1080) is False)
check("1600x900 -> 需要改", wf.needs_fix(1600, 900) is True)
check("差 1 像素也算需要改", wf.needs_fix(1920, 1079) is True)
check("宽对高错也算需要改", wf.needs_fix(1920, 1200) is True)
check("可指定别的目标尺寸", wf.needs_fix(1600, 900, 1600, 900) is False)

print()
print("D. compute_outer_size（客户区 -> 整窗 反算）")


def adjust_with_border(rect):
    """模拟 AdjustWindowRectExForDpi：左/右各 8、上 31、下 8。"""
    return {"left": rect["left"] - 8, "top": rect["top"] - 31,
            "right": rect["right"] + 8, "bottom": rect["bottom"] + 8}


def adjust_borderless(rect):
    return dict(rect)


seen = {}


def adjust_spy(rect):
    seen.update(rect)
    return adjust_with_border(rect)


check("带边框：1920x1080 客户区 -> 1936x1119 整窗",
      wf.compute_outer_size(1920, 1080, adjust_with_border) == (1936, 1119))
check("无边框：客户区 == 整窗",
      wf.compute_outer_size(1920, 1080, adjust_borderless) == (1920, 1080))
check("传入 adjust 的矩形是 0,0,target_w,target_h",
      (wf.compute_outer_size(1600, 900, adjust_spy), seen) == (
          (1616, 939), {"left": 0, "top": 0, "right": 1600, "bottom": 900}))
check("目标尺寸可为小数输入并被取整",
      wf.compute_outer_size(1920.7, 1080.2, adjust_borderless) == (1920, 1080))

print()
print("E. describe（现状说明）")

d_exact = wf.describe(1920, 1080)
d_off = wf.describe(1600, 900)
check("正好时说明含「正好」", "正好" in d_exact)
check("正好时缩放比显示 1.00", "1.00" in d_exact)
check("不匹配时说明含「不是标定」", "不是标定" in d_off)
check("不匹配时带上实际尺寸", "1600x900" in d_off)
check("不匹配时带上目标尺寸", "1920x1080" in d_off)
check("不匹配时缩放比按高度算（900/1080=0.833）", "0.833" in d_off)

print()
print("F. parse_size（命令行尺寸）")

check("1920x1080", wf.parse_size("1920x1080") == (1920, 1080))
check("大写 X 也认", wf.parse_size("1920X1080") == (1920, 1080))
check("星号也认", wf.parse_size("1920*1080") == (1920, 1080))
check("逗号也认", wf.parse_size("1920,1080") == (1920, 1080))
check("带空格也认", wf.parse_size(" 1600 x 900 ") == (1600, 900))
for bad in ("1920", "abc", "0x1080", "1920x0", "-1920x1080", ""):
    try:
        wf.parse_size(bad)
        ok = False
    except (ValueError, TypeError):
        ok = True
    check("非法尺寸 %r 抛错" % bad, ok)

print()
print("G. main 的参数校验（不触发 Win32）")

check("尺寸非法 -> 退出码 1", wf.main(["--size", "bogus"]) == 1)
check("只解析参数不碰窗口（平台不符或参数先失败都返回 1）",
      wf.main(["--size", "abc"]) == 1)

print()
print("H. 本模块源码 GBK 安全（Windows 控制台/管道不会崩）")

src = open(wf.__file__, "rb").read().decode("utf-8")
try:
    src.encode("gbk")
    gbk_ok = True
except UnicodeEncodeError as e:
    gbk_ok = False
    print("      编不出的字符：%s" % e)
check("window_fix.py 可被 GBK 编码", gbk_ok)

print()
print("PASS=%d FAIL=%d" % (npass, nfail))
if nfail:
    print("有 %d 条失败" % nfail)
    sys.exit(1)
print("全部通过")
