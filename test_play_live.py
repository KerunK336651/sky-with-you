# -*- coding: utf-8 -*-
"""真机弹琴测试（独立脚本，不需要先起 MCP）。

步骤：
  1. 进光遇，打开任意乐器（15 键按键界面可见）；
  2. 切到 PowerShell，运行：
       py test_play_live.py [歌名] [倍速]
     例如：py test_play_live.py 小星星
           py test_play_live.py 小星星 1.2
     不传参数默认弹《小星星》、1.0 倍速；
  3. 脚本会倒数 5 秒，期间切回【光遇乐器界面】并保持在前台，然后自动弹奏。

后端自动选择：检测到 Arduino 用硬件键盘，没检测到用软件模拟（README 已说明软件可能吞键）。
播放中途想停：Ctrl+C 或另开窗口调 stop_song。
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("skymcp", ROOT / "sky-mcp-server.py")
m = importlib.util.module_from_spec(spec)
sys.modules["skymcp"] = m
spec.loader.exec_module(m)

song = sys.argv[1] if len(sys.argv) > 1 else "小星星"
speed = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

c = m.PcSkyController(m.ServerConfig())

info = c.list_songs(limit=100000)
print("曲库共 %s 首；准备弹《%s》（%.1f 倍速）" % (info.get("total", 0), song, speed))

print("5 秒后开始，请切到【光遇乐器界面】并保持在前台...")
for i in range(5, 0, -1):
    print(i)
    time.sleep(1)

r = c.play_song(song, speed=speed, auto_focus=False)
print("play_song ->", r)

last = None
while True:
    st = c.song_status()
    key = (st.get("status"), st.get("event_index"))
    if key != last:
        print("进度: %s  %s/%s 事件  %s%%" % (
            st.get("status"), st.get("event_index"), st.get("event_total"),
            st.get("progress_pct")))
        last = key
    if st.get("status") == "idle":
        break
    time.sleep(1.0)
print("播放结束。")
