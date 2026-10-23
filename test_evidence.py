# -*- coding: utf-8 -*-
"""test_evidence.py — 取证帧的命名与清理（离线，只在 _tmp/ 下造临时文件）。

背景（2026-10-06 真机发现）：flip_debug 里 80 张 chat_*.jpg 全是某天的 22:xx/23:xx，
而 flip_snapshot_keep=80。新帧按名字排序时排在它们**前面**，于是
`sorted(glob(...))[:-keep]` 取到的第一个就是刚写的那张 —— 写一张删一张，
日志却已经打印了"存证: ...jpg"，证据静默丢失。
本测试把这条回归钉住。
"""
import os
import shutil
import sys
import time

import panel_detector as PD

npass = 0
nfail = 0


def check(desc, ok):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    print("  [%s] %s" % ("PASS" if ok else "FAIL", desc))


TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp", "test_evidence_dir")

print("A. snapshot_name（命名）")

WHEN = time.mktime(time.strptime("2026-10-06 20:13:31", "%Y-%m-%d %H:%M:%S"))
M = {"chat_band": 1.0, "chat_bright": 0.001}
n1 = PD.snapshot_name("chat", WHEN, "on", M)
check("日期进入文件名（年月日_时分秒）", "20261006_201331" in n1)
check("保留 on/off 标记", "_on_" in n1)
check("保留暗带/亮点指标", n1.endswith("_b100_t1.jpg"))
check("以 chat_ 开头（既有 glob 仍匹配）", n1.startswith("chat_"))
check("整体与预期一致", n1 == "chat_20261006_201331_on_b100_t1.jpg")
check("后缀可空（dump_evidence 用）",
      PD.snapshot_name("reopen", WHEN, "", M) == "reopen_20261006_201331_b100_t1.jpg")
check("指标缺省当 0", PD.snapshot_name("chat", WHEN, "off") ==
      "chat_20261006_201331_off_b0_t0.jpg")
check("名字确定性（同输入同输出）", PD.snapshot_name("chat", WHEN, "on", M) == n1)
check("纯 ASCII（中文路径会让 cv2 静默失败）",
      all(ord(c) < 128 for c in n1))
check("按文件名排序 == 按时间排序（这才是修好的关键）",
      PD.snapshot_name("chat", WHEN, "on") <
      PD.snapshot_name("chat", WHEN + 60, "on"))

print()
print("B. prune_snapshots（清理）")


def reset_dir():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)


def touch(name, mtime):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(b"x")
    os.utime(p, (mtime, mtime))
    return p


reset_dir()
base = time.time()
touch("chat_225728_on_b100_t3.jpg", base - 500)   # 上一轮 22:xx 的老帧
touch("chat_233000_on_b100_t3.jpg", base - 400)
touch("chat_233600_on_b100_t3.jpg", base - 300)
touch("chat_201331_on_b100_t1.jpg", base - 10)    # 刚写的新帧（名字排最前！）
left = sorted(os.listdir(TMP))
check("造好 4 个文件", len(left) == 4)
deleted = PD.prune_snapshots(TMP, "chat", 2)
remain = sorted(os.listdir(TMP))
check("删掉 2 个", deleted == 2)
check("**刚写的那张（名字排最前）被保留了** —— 这就是回归点",
      "chat_201331_on_b100_t1.jpg" in remain)
check("保留的是按 mtime 最新的两张",
      remain == ["chat_201331_on_b100_t1.jpg", "chat_233600_on_b100_t3.jpg"])

# 不超上限时一张都不删
reset_dir()
touch("chat_a_on_b1_t1.jpg", base - 10)
check("数量未超上限 -> 不删", PD.prune_snapshots(TMP, "chat", 80) == 0)
check("文件还在", len(os.listdir(TMP)) == 1)

# keep = 0 -> 全删（原写法 old[:-0] == [] 会一张都不删，是个隐藏 bug）
reset_dir()
touch("chat_a_on_b1_t1.jpg", base - 10)
touch("chat_b_on_b1_t1.jpg", base - 5)
check("keep<=0 -> 全删（修正 old[:-0] 的空切片坑）",
      PD.prune_snapshots(TMP, "chat", 0) == 2 and os.listdir(TMP) == [])

# 只清理同名 tag，别误删别的 tag
reset_dir()
touch("chat_a_on_b1_t1.jpg", base - 30)
touch("chat_b_on_b1_t1.jpg", base - 20)
touch("chat_c_on_b1_t1.jpg", base - 10)
touch("reopen_x_b1_t1.jpg", base - 100)
PD.prune_snapshots(TMP, "chat", 1)
remain = sorted(os.listdir(TMP))
check("只动 chat_*，reopen_* 不受影响",
      remain == ["chat_c_on_b1_t1.jpg", "reopen_x_b1_t1.jpg"])

# 文件在 glob 与 unlink 之间消失 -> 不抛异常
reset_dir()
touch("chat_a_on_b1_t1.jpg", base - 10)
check("目录不存在也不抛异常", PD.prune_snapshots(os.path.join(TMP, "nope"), "chat", 1) == 0)
check("keep=None 视为不清理", PD.prune_snapshots(TMP, "chat", None) == 0)

print()
print("C. 源码里不再有「按文件名排序清理」的旧写法")

src = open(PD.__file__, "rb").read().decode("utf-8")
check("没有 sorted(glob(...)) 直接交给 unlink 的旧写法",
      "for p in old[:-self.cfg.flip_snapshot_keep]" not in src)
check("两处都改走 prune_snapshots", src.count("prune_snapshots(") >= 3)
check("取证命名走 snapshot_name", src.count("snapshot_name(") >= 3)

print()
print("D. GBK 安全")
try:
    src.encode("gbk")
    gbk_ok = True
except UnicodeEncodeError as e:
    gbk_ok = False
    print("      编不出的字符：%s" % e)
check("panel_detector.py 仍可被 GBK 编码", gbk_ok)

shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASS=%d FAIL=%d" % (npass, nfail))
if nfail:
    print("有 %d 条失败" % nfail)
    sys.exit(1)
print("全部通过")
