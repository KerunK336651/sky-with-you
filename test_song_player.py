# -*- coding: utf-8 -*-
"""test_song_player.py — sky_song_player 曲谱解析与播放线程的离线单测。

不碰硬件、不碰游戏、不联网：发键用 mock chord_fn 记录，播放用极小的时间片。
覆盖：
  1) match_key 忽略轨道前缀（1KeyN/2KeyN）、识别黑键负号、返回 None
  2) notes_to_events 聚合同时刻和弦、gap 计算、空键过滤、时长默认与 cap
  3) load_sheet 解析随仓库的示例曲谱
  4) find_song 精确/子串/多匹配/不存在
  5) display_name 清洗
  6) SongPlayer 正常播放、倍速、暂停/继续、停止（mock 发键）
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sky_song_player as sp

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, detail))


def make_sheet(events):
    return {"name": "t", "file": "x", "author": "", "transcribed_by": "",
            "total_ms": sum(e.gap_ms for e in events), "events": events}


# ---------- 1) match_key ----------
check("match_key 忽略旋律轨道 1Key", sp.match_key("1Key3") == "Key3")
check("match_key 忽略和声轨道 2Key", sp.match_key("2Key7") == "Key7")
check("match_key 识别黑键负号", sp.match_key("1Key-2") == "Key-2")
check("match_key 无键返回 None", sp.match_key("hold") is None)

# ---------- 2) notes_to_events ----------
def N(t, key, dur=None):
    d = {"time": t, "key": key}
    if dur is not None:
        d["duration"] = dur
    return d


# 同一时刻两个不同轨道的音 -> 和弦（顺序按 KeyN）
evs = sp.notes_to_events([N(0, "1Key3"), N(0, "2Key7"), N(500, "1Key0")])
check("同时间聚合成和弦", evs[0].keys == ["o", "k"] and evs[0].is_chord,
      "got %r" % evs[0].keys)
check("和弦 gap 取到下一时刻", evs[0].gap_ms == 500)
check("单音事件", evs[1].keys == ["y"] and evs[1].gap_ms == 0)

# 无映射键（黑键/扩展键）被过滤
evs2 = sp.notes_to_events([N(0, "1Key20"), N(100, "1Key0")])
check("无映射键被过滤，gap 正确", len(evs2) == 1 and evs2[0].keys == ["y"] and evs2[0].gap_ms == 0)

# 时长：无 duration 用默认 220；duration 不超过 gap
evs3 = sp.notes_to_events([N(0, "1Key0"), N(100, "1Key1")])  # gap 100，默认 220 被 cap
check("默认 hold 被 cap 到 gap", sp._hold_ms_for(evs3[0]) == 100, "got %d" % sp._hold_ms_for(evs3[0]))
evs4 = sp.notes_to_events([N(0, "1Key0", 50), N(1000, "1Key1")])
check("显式 duration 生效", sp._hold_ms_for(evs4[0]) == 50)
evs5 = sp.notes_to_events([N(0, "1Key0", 5), N(1000, "1Key1")])
check("hold 最小 40ms", sp._hold_ms_for(evs5[0]) == 40, "got %d" % sp._hold_ms_for(evs5[0]))

# 同一时刻重复键去重
evs6 = sp.notes_to_events([N(0, "1Key0"), N(0, "2Key0")])
check("同时间同键去重", len(evs6[0].keys) == 1)

# ---------- 3) 解析示例曲谱 ----------
songs_dir = os.path.join(HERE, "songs")
if os.path.isdir(songs_dir):
    m = sp.find_song("小星星", [songs_dir])
    check("找到小星星示例", m is not None and m["name"].startswith("小星星"))
    if m:
        sheet = sp.load_sheet(m["file"])
        check("小星星首音 do=y", sheet["events"][0].keys == ["y"])
        check("小星星含正确的 sol(p)", any(e.keys == ["p"] for e in sheet["events"]))
    scale = sp.find_song("音阶", [songs_dir])
    if scale:
        s2 = sp.load_sheet(scale["file"])
        all15 = [k for e in s2["events"][:15] for k in e.keys]
        check("音阶覆盖全部 15 键", all15 == list(sp.KEY_MAP.values()),
              "got %d" % len(set(all15)))
else:
    check("songs 目录存在", False, "songs/ 缺失")

# ---------- 4) find_song ----------
with tempfile.TemporaryDirectory() as td:
    open(os.path.join(td, "斑马斑马-138625.txt"), "w", encoding="utf-8").write("[]")
    open(os.path.join(td, "小星星.txt"), "w", encoding="utf-8").write("[]")
    check("精确命中", sp.find_song("小星星", [td]) is not None)
    check("子串唯一命中", sp.find_song("斑马", [td]) is not None)
    open(os.path.join(td, "斑马2.txt"), "w", encoding="utf-8").write("[]")
    check("多匹配返回 None", sp.find_song("斑马", [td]) is None)
    check("不存在返回 None", sp.find_song("不存在的歌", [td]) is None)

# ---------- 4b) 近似查找（修 2026-11-12 真机弹琴 not_found） ----------
# 事故：玩家要《一滴泪的时间》、曲库里只有《给我一首歌的时间》，
# find_song 查不到 -> not_found 连发 7 次，AI 拿不到原因还以为自己在弹。
check("归一化：去编号后缀", sp.normalize_song_name("给我一首歌的时间-#202312") == "给我一首歌的时间")
check("归一化：去首尾空格", sp.normalize_song_name("  晴天 ") == "晴天")
check("归一化：切掉括号版本/作者尾缀",
      sp.normalize_song_name("一滴泪的时间（段）【by小社】") == "一滴泪的时间")
check("归一化：切掉 (v1) 这类尾缀", sp.normalize_song_name("晴天(v1)") == "晴天")
check("相似度：OCR 错名能对上「带尾缀的真名」",
      sp.song_name_similarity("给我滴泪的时间", "一滴泪的时间（段）【by小社】") >= 0.42)
check("归一化：None/空串安全", sp.normalize_song_name(None) == "" and sp.normalize_song_name("") == "")
check("相似度：归一化后相等 = 1.0", sp.song_name_similarity("晴天", "晴天-#234302") == 1.0)
check("相似度：真机事故原形达标",
      sp.song_name_similarity("给我滴泪的时间", "给我一首歌的时间") >= 0.42)
check("相似度：无关短名不达标（晴天 vs 雨天）",
      sp.song_name_similarity("晴天", "雨天") < 0.42)
check("相似度：字符集相近但不同歌不达标（一剪梅 vs 梅花）",
      sp.song_name_similarity("一剪梅", "梅花") < 0.42)
check("相似度：空串返回 0", sp.song_name_similarity("", "晴天") == 0.0)
check("相似度：对称", abs(sp.song_name_similarity("给我滴泪的时间", "给我一首歌的时间")
                        - sp.song_name_similarity("给我一首歌的时间", "给我滴泪的时间")) < 1e-9)

with tempfile.TemporaryDirectory() as td_sim:
    open(os.path.join(td_sim, "给我一首歌的时间-#202312.txt"), "w", encoding="utf-8").write("[]")
    open(os.path.join(td_sim, "晴天.txt"), "w", encoding="utf-8").write("[]")
    _sim = sp.find_song_similar("给我滴泪的时间", [td_sim])
    check("近似：给出最像的那首", len(_sim) >= 1 and _sim[0]["name"] == "给我一首歌的时间")
    check("近似：每条带 score", all("score" in x for x in _sim))
    check("近似：按分数降序",
          all(_sim[i]["score"] >= _sim[i + 1]["score"] for i in range(len(_sim) - 1)))
    check("近似：无关查询返回空（不乱给候选）", sp.find_song_similar("zzzzz", [td_sim]) == [])
    check("近似：不改变 find_song 原有语义", sp.find_song("给我滴泪的时间", [td_sim]) is None)
    check("近似：limit 生效",
          len(sp.find_song_similar("给我一首歌的时间", [td_sim], limit=1)) <= 1)

# ---------- 5) display_name ----------
check("display_name 去编号", sp.display_name("斑马斑马-#138625") == "斑马斑马")
check("display_name 去扩展名", sp.display_name("小星星.txt") == "小星星")

# ---------- 6) SongPlayer 播放控制（mock 发键） ----------
def make_ev(spec):
    notes = []
    t = 0
    for keys, gap in spec:
        if isinstance(keys, str):
            keys = [keys]
        notes.append(sp.NoteEvent(keys=keys, gap_ms=gap, duration_ms=min(gap, 30) if gap else 30))
        t += gap
    return notes


# 6.1 正常播放：三个事件都应被调用，顺序正确
calls = []
def chord_fn(keys, hold):
    calls.append((tuple(keys), hold))

sheet = make_sheet(make_ev([("y", 20), ("u", 20), (["o", "j"], 0)]))
p = sp.SongPlayer(chord_fn=chord_fn)
p.play(sheet, speed=1)
p._thread.join(timeout=3)
st = p.state()
check("播放完成状态", st["status"] == "finished", st["status"])
check("三个事件全部发键", len(calls) == 3, "got %d" % len(calls))
check("和弦事件被发出", calls and calls[2][0] == ("o", "j"), "got %r" % calls)
check("完成进度 100", st["progress_pct"] == 100, "got %s" % st["progress_pct"])

# 6.2 倍速：hold 与 gap 按速度缩放
calls2 = []
p2 = sp.SongPlayer(chord_fn=lambda k, h: calls2.append((tuple(k), h)))
sheet2 = make_sheet(make_ev([("y", 40), ("u", 0)]))
p2.play(sheet2, speed=2)
p2._thread.join(timeout=3)
# 首事件 hold=min(40,40)=40，speed2 -> 20
check("倍速缩放 hold", calls2 and calls2[0][1] == 20, "got %r" % calls2)

# 6.3 停止：长曲播放后立即停止，应停在前面
p3 = sp.SongPlayer(chord_fn=lambda k, h: time.sleep(0.001))
long_spec = [("y", 300)] * 20
p3.play(make_sheet(make_ev(long_spec)), speed=1)
time.sleep(0.05)
p3.stop()
check("停止后状态 stopped", p3.state()["status"] == "stopped", p3.state()["status"])
check("停止后未播完整首", p3.state()["progress_pct"] < 100, "got %s" % p3.state()["progress_pct"])

# 6.4 暂停/继续
p4 = sp.SongPlayer(chord_fn=lambda k, h: None)
p4.play(make_sheet(make_ev([("y", 100), ("u", 100), ("i", 0)])), speed=1)
p4.pause()
check("暂停状态", p4.state()["status"] == "paused", p4.state()["status"])
p4.resume()
check("继续后 playing", p4.state()["status"] == "playing", p4.state()["status"])
p4.stop()

# 6.5 发键报错应停止播放并记录错误
def bad_chord(keys, hold):
    raise RuntimeError("simulated send failure")
p5 = sp.SongPlayer(chord_fn=bad_chord)
p5.play(make_sheet(make_ev([("y", 20), ("u", 0)])), speed=1)
p5._thread.join(timeout=3)
st5 = p5.state()
check("发键失败进入 error", st5["status"] == "error", st5["status"])
check("记录了错误信息", "simulated send failure" in st5["last_error"])

print("=" * 60)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
