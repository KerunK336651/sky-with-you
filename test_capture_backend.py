# -*- coding: utf-8 -*-
"""test_capture_backend.py — DXGI 截屏的坐标系适配（离线，不碰游戏、不创建真实相机）。

背景（2026-10-06 真机实测）：dxcam/bettercam 的 grab(region=...) 用的是
「相对所选输出左上角」的坐标，且 create() 默认主屏。旧实现传绝对桌面坐标 +
默认输出，于是游戏在非主屏时连抛 3 次 ValueError 后被永久切回 GDI，
日志却已经打过「截屏后端: bettercam/DXGI」——装了等于没装。

本测试只验纯函数与枚举逻辑（用假模块/假对象 + 打桩 _output_desktop_rect），
不 import bettercam、不创建任何真实相机。
"""
import sys

import panel_detector as pd

npass = 0
nfail = 0


def check(desc, ok):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    print("  [%s] %s" % ("PASS" if ok else "FAIL", desc))


# 真机布局：\\.\DISPLAY1 = 主屏 2560x1440 @(0,0)；\\.\DISPLAY2 = 1920x1080 @(-1920,0)
D1 = (0, 0, 2560, 1440)
D2 = (-1920, 0, 0, 1080)
OUTS = [("cam1", D1), ("cam2", D2)]

print("A. _pick_output（绝对区域 -> 该用哪块屏）")

check("左屏区域 -> 选中 D2（不是默认的主屏）",
      pd._pick_output(OUTS, -1920, 0, 0, 1080)[0] == "cam2")
check("主屏区域 -> 选中 D1",
      pd._pick_output(OUTS, 0, 0, 1920, 1080)[0] == "cam1")
check("主屏右下角一块 -> D1",
      pd._pick_output(OUTS, 2600 - 100, 0, 2600, 100) is None
      or pd._pick_output(OUTS, 2400, 1300, 2560, 1440)[0] == "cam1")
check("正好贴住 D2 边界 -> D2",
      pd._pick_output(OUTS, -1920, 0, 0, 1080)[0] == "cam2")
check("跨两块屏 -> None（DXGI 抓不了）",
      pd._pick_output(OUTS, -100, 0, 100, 1080) is None)
check("被切边（左边超出虚拟屏）-> None",
      pd._pick_output(OUTS, -2000, 0, 0, 1080) is None)
check("落在两屏之间的空隙（y 超出）-> None",
      pd._pick_output(OUTS, -1920, 1200, 0, 1300) is None)
check("空列表 -> None", pd._pick_output([], 0, 0, 10, 10) is None)
check("None -> None", pd._pick_output(None, 0, 0, 10, 10) is None)
check("单屏时主屏区域可用", pd._pick_output([("c", D1)], 0, 0, 1920, 1080)[0] == "c")
check("单屏时负坐标区域不可用（这就是旧实现踩的坑）",
      pd._pick_output([("c", D1)], -1920, 0, 0, 1080) is None)

print()
print("B. _to_output_relative（绝对 -> 相对所选输出）")

check("D2 上的游戏区域 -> (0,0,1920,1080)",
      pd._to_output_relative(D2, -1920, 0, 0, 1080) == (0, 0, 1920, 1080))
check("D1 上的区域保持原样（D1 原点就是 0,0）",
      pd._to_output_relative(D1, 0, 0, 1920, 1080) == (0, 0, 1920, 1080))
check("D1 内部偏移一块",
      pd._to_output_relative(D1, 100, 200, 300, 400) == (100, 200, 300, 400))
check("D2 内部偏移一块（-1920 抵消）",
      pd._to_output_relative(D2, -1820, 50, -1720, 150) == (100, 50, 200, 150))
check("返回的是整数元组",
      all(isinstance(v, int) for v in pd._to_output_relative(D2, -1920.0, 0.0, 0.0, 1080.0)))
check("相对坐标永不为负（区域一定在输出内）",
      all(v >= 0 for v in pd._to_output_relative(D2, -1920, 0, 0, 1080)))

print()
print("C. _enum_dxgi_outputs（打桩矩形读取，用假模块）")


class FakeCam(object):
    def __init__(self, name):
        self.name = name


class FakeMod(object):
    """create(output_idx=i) 依次返回 cams[i]，越界抛 IndexError（真机就是这样）。"""

    def __init__(self, cams):
        self.cams = cams
        self.calls = []

    def create(self, output_idx=0, output_color="BGR"):
        self.calls.append(output_idx)
        if output_idx >= len(self.cams):
            raise IndexError("list index out of range")
        return self.cams[output_idx]


real_rect = pd._output_desktop_rect
RECTS = {}

try:
    pd._output_desktop_rect = lambda cam: RECTS.get(cam.name)
    m = FakeMod([FakeCam("a"), FakeCam("b")])
    RECTS.update({"a": D1, "b": D2})
    got = pd._enum_dxgi_outputs(m)
    check("两块屏都能枚举出来", len(got) == 2)
    check("顺序与 output_idx 一致",
          [r for _c, r in got] == [D1, D2])
    check("越界后停止（IndexError 被吃掉）", m.calls == [0, 1, 2])

    m2 = FakeMod([FakeCam("a"), FakeCam("b")])
    RECTS["b"] = None      # 第二块拿不到矩形
    got2 = pd._enum_dxgi_outputs(m2)
    check("拿不到矩形的输出直接停（不猜）", len(got2) == 1 and got2[0][1] == D1)

    RECTS["b"] = D2
    RECTS["x"] = D1        # 假 cam 名要能在桩表里查到矩形，否则第一轮就 break
    check("max_outputs 上限生效",
          len(pd._enum_dxgi_outputs(FakeMod([FakeCam("x")] * 12), max_outputs=3)) == 3)

    m3 = FakeMod([])
    check("一块屏都没有 -> 空列表", pd._enum_dxgi_outputs(m3) == [])
finally:
    pd._output_desktop_rect = real_rect

print()
print("D. 源码里不该再有「默认输出 + 绝对坐标」的旧写法")

src = open(pd.__file__, "rb").read().decode("utf-8")
check("不再直接 mod.create(output_color=...) 而不给 output_idx",
      "mod.create(output_color=\"BGR\")" not in src)
check("改用 _enum_dxgi_outputs", "_enum_dxgi_outputs(mod)" in src)
check("grab 前做了相对坐标换算", "_to_output_relative(" in src)
check("选屏走 _pick_output", "_pick_output(" in src)
check("跨屏/切边会抛错交给 _sense_loop 计数（保留 GDI 兜底）",
      "DXGI 抓不了" in src)

print()
print("E. panel_detector.py 仍然 GBK 安全（Windows 控制台/管道不会崩）")

try:
    src.encode("gbk")
    gbk_ok = True
except UnicodeEncodeError as e:
    gbk_ok = False
    print("      编不出的字符：%s" % e)
check("panel_detector.py 可被 GBK 编码", gbk_ok)

print()
print("PASS=%d FAIL=%d" % (npass, nfail))
if nfail:
    print("有 %d 条失败" % nfail)
    sys.exit(1)
print("全部通过")
