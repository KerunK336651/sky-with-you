# -*- coding: utf-8 -*-
"""
星河（sky-with-you）弹琴模块 —— 曲谱解析、键位表、播放时序。

##########################################################################
# 署名与许可（重要）
# 本文件中「曲谱格式解析、15 键键位表 KEY_MAP、按时间聚合成和弦、播放时序」
# 改编自 windhide 的开源项目 SkyMusicPlay-for-Windows（星星弹琴软件）：
#   仓库 : https://github.com/windhide/SkyMusicPlay-for-Windows
#   作者 : windhide
#   许可 : CC BY-NC 4.0（署名-非商业性使用）
#   授权 : 已获作者本人同意"可以照搬，标记出处就可以"
#          （授权聊天截图见 docs/credits/ 目录）
#
# 本文件中的播放线程在其 ControlledThread 基础上重写，并把"发键"改为通过
# 注入的 chord_fn 回调交给 sky-mcp-server 的统一按键后端（Arduino 真 USB HID
# 优先 / pydirectinput 软件兜底），未照搬其 PostMessageW 后台发键实现。
#
# 曲谱（.txt）本身多为社区用户转录，版权归原作者/转录者所有，不在本开源
# 仓库中分发；songs/ 仅含少量自制/公有领域示例。
##########################################################################
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 15 键键位表（照搬自 windhide static/global_variable.py 的 keyMap）
#
# 光遇标准 15 键键盘乐器为 C 大调自然音阶（do re mi fa sol la si 七声），
# 三排物理按键对应（MIDI 音高，Key0=C4 … Key14=C6）：
#   上排 y u i o p = C4 D4 E4 F4 G4（Key0..Key4）
#   中排 h j k l ; = A4 B4 C5 D5 E5（Key5..Key9）
#   下排 n m , . / = F5 G5 A5 B5 C6（Key10..Key14）
# 曲谱里的 "1KeyN"/"2KeyN" 前缀 1/2 是演奏轨道（1=旋律 2=和声），
# 不是两排键；这里统一用正则只取 KeyN。
# 扩展键位（Key-14..Key-1、Key15..Key28，黑键/更高音）在标准键盘上不存在，不映射。
# ---------------------------------------------------------------------------
KEY_MAP: dict[str, str] = {
    "Key0": "y", "Key1": "u", "Key2": "i", "Key3": "o", "Key4": "p",
    "Key5": "h", "Key6": "j", "Key7": "k", "Key8": "l", "Key9": ";",
    "Key10": "n", "Key11": "m", "Key12": ",", "Key13": ".", "Key14": "/",
}

# 所有可弹奏的物理键（顺序与三排一致），供测试/校验使用。
SKY_NOTE_KEYS: tuple[str, ...] = tuple(KEY_MAP.values())

# 曲谱键名形如 "1Key0"、"2Key13"、"Key7"；只捕获 KeyN（N 可负），
# 前面的轨道号 1/2 被忽略。照搬自 windhide path_util.match_key。
_KEY_RE = re.compile(r"(Key-?\d+)")

# 曲谱文件名末尾常带总时长后缀，例如「斑马斑马-#138625.txt」
_DURATION_SUFFIX_RE = re.compile(r"-#\d+$")

# 默认音符保持时长（曲谱没有 duration 字段时使用）。
# 太短容易被游戏吞键、听起来像断音；过长会和下一个音重叠。
DEFAULT_NOTE_HOLD_MS = int(os.environ.get("SKY_SONG_HOLD_MS", "220"))
# 和弦/单音最短保持，保证按键事件能被游戏识别。
MIN_NOTE_HOLD_MS = 40
# 倍速缩放后的最短保持（允许快速播放，后端软件层也有自己的最小按压兜底）。
PLAY_MIN_HOLD_MS = 10

# ── 乐器档位（2026-11-01）─────────────────────────────────────────────
# 光遇里不同乐器对"按住"的反应不同：钢琴按住不延音，小提琴/笛子/口琴能拉长。
# 所以同一份曲谱，钢琴要把音压短（长按没意义还会拖慢后续），管弦/吹奏则按到下一个音（legato）。
# 默认按钢琴处理 —— 它是"短"的那一侧，宁可断一点也不要拖音。
INSTRUMENT_PROFILES = {
    "piano":     {"label": "钢琴",   "sustain": False, "max_hold_ms": 110},
    "violin":    {"label": "小提琴", "sustain": True,  "max_hold_ms": 0},
    "flute":     {"label": "笛子",   "sustain": True,  "max_hold_ms": 0},
    "harmonica": {"label": "口琴",   "sustain": True,  "max_hold_ms": 0},
}


def instrument_profile(kind: str | None) -> dict:
    """乐器名 → 档位（sustain 是否允许长按 / max_hold_ms 上限）。未知一律按钢琴。"""
    return dict(INSTRUMENT_PROFILES.get(str(kind or "").strip().lower(),
                                        INSTRUMENT_PROFILES["piano"]))


def humanize_params(level) -> dict:
    """拟人化档位 → (抖动毫秒范围, 呼吸毫秒范围, 每几个音呼吸一次)。

    0 = 关闭（默认，精确按谱）；1 = 轻微（±8~12ms + 每 8~16 个音呼吸 80~150ms）；
    2 = 明显（±14~22ms + 每 6~10 个音呼吸 150~260ms，适合慢曲）。
    纯函数，便于离线单测；随机取值在播放线程里做。
    """
    try:
        lvl = int(level)
    except Exception:
        lvl = 0
    if lvl <= 0:
        return {"jitter_ms": (0, 0), "breath_ms": (0, 0), "breath_every": (0, 0)}
    if lvl == 1:
        return {"jitter_ms": (8, 12), "breath_ms": (80, 150), "breath_every": (8, 16)}
    return {"jitter_ms": (14, 22), "breath_ms": (150, 260), "breath_every": (6, 10)}


def match_key(note_key: str) -> str | None:
    """从曲谱音符键名中提取 KeyN（忽略 1/2 轨道前缀）。无匹配返回 None。"""
    if not note_key:
        return None
    m = _KEY_RE.search(note_key)
    return m.group(1) if m else None


@dataclass
class NoteEvent:
    """一个播放时刻：同一时间点的若干键（单音/和弦）及其到下一时刻的间隔。"""
    keys: list[str]                      # 物理键字符，例如 ["y"] 或 ["y", "k"]
    gap_ms: int                          # 本时刻到下一时刻的间隔（最后一个为 0）
    duration_ms: int = 0                 # 曲谱指定的保持时长（0 表示未指定）
    time_ms: int = 0                     # 该时刻在曲谱中的绝对时间（毫秒）

    @property
    def is_chord(self) -> bool:
        return len(self.keys) > 1


def _read_text_auto(path: Path) -> str:
    """读曲谱文本：先按 BOM/NUL 判 UTF-16，再退回 UTF-8 -> GBK。

    2026-11-13：真实曲库里混着 **UTF-16（带 BOM，Windows 记事本"Unicode"保存 /
    星星弹琴导出）** 的谱。原来只试 utf-8-sig/utf-8/gbk，这类文件会被 gbk 解成
    夹着 NUL 的乱码，json 只报 "Expecting value: line 1 column 1"，极难定位
    （实测 7314 首新曲里 7313 首中招）。
    """
    raw = path.read_bytes()
    # 1) 有 BOM：utf-16 会自动识别字节序并吃掉 BOM
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    # 2) 无 BOM 但 NUL 字节很多 -> 多半是 UTF-16LE（纯 ASCII 的 JSON 里每隔一字节一个 0）
    sample = raw[:400]
    if sample and sample.count(0) >= max(2, len(sample) // 4):
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc).lstrip("\ufeff")
            except UnicodeDecodeError:
                continue
    # 3) 常规 UTF-8 / GBK
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_song_notes(payload: object) -> list[dict]:
    """从 Sky Music Studio 的 JSON 结构里取出 songNotes。

    常见结构是 [{...,"songNotes":[...]}]（数组包一个对象），也兼容直接给
    对象 {..,"songNotes":[..]} 或直接给音符数组。
    """
    if isinstance(payload, list):
        if not payload:
            return []
        # 多数文件是 [song]；个别可能是 [note, note, ...]
        first = payload[0]
        if isinstance(first, dict) and "songNotes" in first:
            return list(first.get("songNotes") or [])
        if isinstance(first, dict) and "key" in first and "time" in first:
            return list(payload)  # type: ignore[arg-type]
        return []
    if isinstance(payload, dict):
        if "songNotes" in payload:
            return list(payload.get("songNotes") or [])
        if "key" in payload and "time" in payload:
            return [payload]
    return []


def load_sheet(path: str | Path) -> dict:
    """加载一首曲谱，返回 {name, author, bpm, events, total_ms}。

    解析失败抛 ValueError。
    """
    p = Path(path)
    text = _read_text_auto(p)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"曲谱不是有效的 JSON：{p.name}（{e}）") from e

    notes = _extract_song_notes(payload)
    meta = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) \
        else (payload if isinstance(payload, dict) else {})
    events = notes_to_events(notes)
    if not events:
        raise ValueError(f"曲谱没有可演奏的音符：{p.name}")

    total_ms = sum(ev.gap_ms for ev in events)
    return {
        "name": meta.get("name") or display_name(p.name),
        "author": meta.get("author", ""),
        "transcribed_by": meta.get("transcribedBy", ""),
        "bpm": meta.get("bpm", 0),
        "events": events,
        "total_ms": total_ms,
        "path": str(p),
    }


def notes_to_events(notes: list[dict]) -> list[NoteEvent]:
    """把原始 songNotes 转成按时序的 NoteEvent（照搬 windhide 的聚合逻辑）。

    - 同一 time 的多个音符聚合成和弦；
    - 无法映射的键（黑键/扩展键）会被丢弃；
    - gap_ms = 下一个时刻 time - 当前时刻 time（最后一个为 0）。
    """
    groups: dict[int, list[str]] = {}
    durations: dict[int, int] = {}
    times: list[int] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        key_n = match_key(str(note.get("key", "")))
        if not key_n:
            continue
        ch = KEY_MAP.get(key_n, "")
        if not ch:
            continue  # 标准 15 键键盘没有的音（黑键/扩展），跳过
        try:
            t = int(round(float(note.get("time", 0))))
        except (TypeError, ValueError):
            continue
        if t not in groups:
            groups[t] = []
            times.append(t)
            durations[t] = 0
        if ch not in groups[t]:
            groups[t].append(ch)
        try:
            dur = int(round(float(note.get("duration", 0) or 0)))
        except (TypeError, ValueError):
            dur = 0
        if dur > durations[t]:
            durations[t] = dur

    times.sort()
    events: list[NoteEvent] = []
    for i, t in enumerate(times):
        gap = (times[i + 1] - t) if i + 1 < len(times) else 0
        events.append(NoteEvent(
            keys=groups[t],
            gap_ms=max(0, gap),
            duration_ms=durations[t],
            time_ms=t,
        ))
    return events


def _hold_ms_for(ev: NoteEvent) -> int:
    """决定一个音符/和弦的实际保持时长。

    - 曲谱给了 duration：取 duration，但不超过到下一音的间隔（避免拖音重叠）；
    - 没给 duration：用默认值，同样不超过间隔；
    - 快速音阶里间隔很短，就保持到接近下一个音（最短 MIN_NOTE_HOLD_MS）。
    """
    gap = ev.gap_ms
    base = ev.duration_ms if ev.duration_ms > 0 else DEFAULT_NOTE_HOLD_MS
    if gap > 0:
        hold = min(base, gap)
    else:
        hold = base
    return max(MIN_NOTE_HOLD_MS, hold)


def display_name(filename: str) -> str:
    """文件名 -> 展示歌名：去扩展名、去末尾 -#时长 后缀。"""
    name = filename
    if name.lower().endswith(".txt"):
        name = name[:-4]
    return _DURATION_SUFFIX_RE.sub("", name).strip()


def list_song_files(dirs: list[str | Path], max_per_dir: int | None = None) -> list[dict]:
    """扫描若干曲谱目录，返回 [{name, file, dir, size}]，按歌名排序。

    只看文件名、不解析 JSON（曲谱库可能上万首，全量解析太慢）。
    用 os.scandir 而不是 Path.glob("*.txt")：11000 首时 glob 要 2 秒（逐个构造
    Path + fnmatch），scandir 只要 50ms。一次点歌要扫两遍（先列版本问玩家、玩家
    回答后再列一次），这里慢一点玩家就能感觉到卡。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for d in dirs:
        if not d:
            continue
        root = Path(d)
        if not root.is_dir():
            continue
        count = 0
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if not e.name.lower().endswith(".txt"):
                continue
            try:
                size = e.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            name = display_name(e.name)
            f = root / e.name
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "file": str(f), "dir": str(root), "size": size})
            count += 1
            if max_per_dir and count >= max_per_dir:
                break
    out.sort(key=lambda x: x["name"])
    return out


def _is_community_song(song: dict) -> bool:
    """是否第三方社区转录谱（文件名带 -#编号 后缀）。

    2026-10-26 把 11075 首社区谱并进 songs/ 后，同名歌会有多个候选
    （例："小星星" 同时命中 小星星-示例.txt 与 小星星-#170676.txt）。社区谱是
    按社区编号命名的转录版，项目自带的示例/自制谱应优先，否则点歌会随机拿到不同版本。
    """
    # 用 stem（去掉 .txt）匹配，因为 _DURATION_SUFFIX_RE 锚定在结尾
    return bool(_DURATION_SUFFIX_RE.search(Path(song["file"]).stem or ""))


def _pick_best(cands: list[dict]) -> dict | None:
    """从候选里挑一个：自制/示例谱优先于社区谱；同类仍命中多个 = 歧义，返回 None。"""
    if not cands:
        return None
    local = [s for s in cands if not _is_community_song(s)]
    pool = local or cands
    if len(pool) == 1:
        return pool[0]
    return None


def find_song(query: str, dirs: list[str | Path]) -> dict | None:
    """在曲谱目录里按歌名找谱：精确（去时长后缀）> 包含（唯一命中）。

    命中多个返回 None（调用方提示用户给更精确的名字）。
    同名时**自制/示例谱优先于社区转录谱**（见 _is_community_song）。
    """
    songs = list_song_files(dirs)
    if not songs:
        return None
    q = query.strip()
    qf = q.casefold()

    def _match(pool: list[dict]) -> dict | None:
        # 1) 完全相等
        exact = [s for s in pool
                 if s["name"].casefold() == qf
                 or display_name(Path(s["file"]).name).casefold() == qf]
        if exact:
            return _pick_best(exact)
        # 2) 子串包含
        hits = [s for s in pool if qf in s["name"].casefold()]
        return _pick_best(hits)

    # 先在项目自带的谱里找，找不到再退到社区转录谱（否则点歌会拿到随机的社区版本）
    local_pool = [s for s in songs if not _is_community_song(s)]
    return _match(local_pool) or _match([s for s in songs if _is_community_song(s)])


# ---------------------------------------------------------------------------
# 近似查找：find_song 是"精确 > 子串"，差一个字就彻底找不到。
# 2026-11-12 真机事故：玩家要《一滴泪的时间》、曲库里只有《给我一首歌的时间》，
# not_found 连发 7 次，AI 因为拿不到失败原因还以为自己在弹（"弹啦，你听听看"）。
# 所以补一条"最像的几首"给调用方去反问玩家。这里**只给建议、不拍板**
# （说哪句台词仍由 AI 决定，与项目"弹不弹/弹哪首由星河判断"的约定一致）。
# ---------------------------------------------------------------------------
_NAME_NOISE_RE = re.compile(
    r"[\s\-—–_()（）\[\]【】《》「」『』、,，.。:：;；!！?？'\"\u201c\u201d\u2018\u2019#]+")
# 括号起的"版本/作者/段"尾缀：一曲多名时歌名主体其实在前面。
#   "一滴泪的时间（段）【by小社】" 的主体是 "一滴泪的时间"
# 不切的话"段by小社"会变成噪声，把 OCR 错名（给我滴泪的时间）的相似度从 0.69 压到 0.41。
_CORE_CUT_RE = re.compile(r"[（(【\[「『]")


def normalize_song_name(name: str) -> str:
    """歌名归一化（纯函数）：去时长后缀、切掉括号尾缀、去标点空白、统一小写。

    例：
      "给我一首歌的时间-#202312"        -> "给我一首歌的时间"
      "一滴泪的时间（段）【by小社】"      -> "一滴泪的时间"
      "ヨスガノソラ メインテーマ -記憶"   -> "ヨスガノソラメインテーマ記憶"
    """
    s = _DURATION_SUFFIX_RE.sub("", str(name or ""))
    s = _CORE_CUT_RE.split(s, 1)[0]      # 只留第一个括号之前的主体
    return _NAME_NOISE_RE.sub("", s).casefold()


def song_name_similarity(a: str, b: str) -> float:
    """两个歌名的相似度 0~1（纯函数）。

    0.55*字符集Jaccard + 0.25*双字组Jaccard + 0.20*长度接近度。

    为什么三项都要：
      * 只算字符集，短名字会被"长度接近"抬高——"晴天" vs "雨天" 曾算到 0.60；
      * 加入双字组后，"晴天"{晴天} 与 "雨天"{雨天} 无共同双字组 -> 0.38（正确淘汰）；
      * 但纯双字组对长句又太严（"给我滴泪的时间"只有 3/10 个共同双字组），
        所以保留字符集项与长度项做平衡。

    实测（随曲库 11077 首）：
      "给我滴泪的时间" vs "给我一首歌的时间" = 0.525  <- 2026-11-12 真机事故原形
      "给我滴泪的时间" vs "给我一首歌的时间_1" 次之
      "晴天"           vs "雨天"             = 0.383（低于默认阈值 0.42，正确淘汰）
      "一剪梅"         vs "梅花"             = 0.271（正确淘汰）
      "晴天"           vs "晴天-#234302"     = 1.000（归一化后相等）
    """
    na, nb = normalize_song_name(a), normalize_song_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sa, sb = set(na), set(nb)
    union = len(sa | sb)
    char_jac = (len(sa & sb) / union) if union else 0.0

    ba = {na[i:i + 2] for i in range(len(na) - 1)}
    bb = {nb[i:i + 2] for i in range(len(nb) - 1)}
    bunion = len(ba | bb)
    bigram_jac = (len(ba & bb) / bunion) if bunion else 0.0

    lr = min(len(na), len(nb)) / max(len(na), len(nb))
    return 0.55 * char_jac + 0.25 * bigram_jac + 0.20 * lr


def find_song_similar(query: str, dirs: list[str | Path], limit: int = 5,
                      min_score: float = 0.42) -> list[dict]:
    """按名字相似度返回"最像的几首"（纯建议，不替代 find_song）。

    只在 find_song / find_song_versions 都查不到时兜底用：调用方拿这份清单去问
    "你说的是不是这首？"。排序稳定（分数降序、自带谱优先、名字字典序），
    每条多带一个 score 字段（0~1）方便调用方自己设门槛。
    """
    songs = list_song_files(dirs)
    q = (query or "").strip()
    if not songs or not q:
        return []
    scored = []
    for s in songs:
        best = 0.0
        for cand_name in (s["name"], display_name(Path(s["file"]).name)):
            best = max(best, song_name_similarity(q, cand_name))
        if best >= min_score:
            scored.append((best, 1 if _is_community_song(s) else 0, s["name"], s))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    out: list[dict] = []
    seen: set = set()
    for sc, _comm, _n, s in scored:
        if s["file"] in seen:
            continue
        seen.add(s["file"])
        item = dict(s)
        item["score"] = round(sc, 4)
        out.append(item)
        if len(out) >= max(0, limit):
            break
    return out


# ---------------------------------------------------------------------------
# 点歌歧义：返回候选清单 + 解析玩家选了第几个
#
# find_song 命中多个时只返回 None（歧义信息丢了），调用方没法问用户"要哪个版本"。
# 这两个函数专门补这一段：find_song_candidates 给出可展示的候选列表，
# resolve_choice 把玩家的回答（序号/中文序号/版本名关键词）映射回某一首。
# ---------------------------------------------------------------------------
def find_song_candidates(query: str, dirs: list[str | Path], limit: int = 6,
                         include_partial: bool = False) -> list[dict]:
    """按歌名找候选（find_song 命中多个时用它列出可选版本）。

    筛选口径与 find_song 完全一致（精确 name/file stem > 子串），但不去歧义：
    - 排序：自带（示例/自制）谱在前、社区转录谱在后，组内按 name —— 与 _pick_best 同口径；
    - 只截取前 limit 条做 load_sheet（解析 JSON 有成本，曲库上万首不能全量解）；
    - 返回项含 index（1-based，给玩家说序号用）、label（可区分版本的显示串）。

    include_partial=True：精确命中之外**同时**带上"名字里包含查询词"的变体
    （"小星星变奏曲" 的裸版 + 署名版、"起风了(v1)/(v2)" 都算同一首歌的版本）。
    默认 False，保持 find_song 的"精确优先"语义不变。
    """
    songs = list_song_files(dirs)
    q = (query or "").strip()
    if not songs or not q:
        return []
    qf = q.casefold()

    exact = [s for s in songs
             if s["name"].casefold() == qf
             or display_name(Path(s["file"]).name).casefold() == qf]
    if include_partial:
        hits = [s for s in songs if qf in s["name"].casefold()]
        pool, seen = [], set()
        for s in exact + hits:          # 精确名排前面，其余变体补在后面
            if s["file"] in seen:
                continue
            seen.add(s["file"])
            pool.append(s)
    else:
        pool = exact or [s for s in songs if qf in s["name"].casefold()]
    # 自带谱优先；同组内按展示名稳定排序（否则不同机器/目录顺序下选中的版本会变）
    pool = sorted(pool, key=lambda s: (1 if _is_community_song(s) else 0, s["name"]))[:max(0, limit)]

    out: list[dict] = []
    for i, s in enumerate(pool):
        community = _is_community_song(s)
        author = transcribed = ""
        total_ms = None
        events = None
        try:
            sheet = load_sheet(s["file"])
            author = str(sheet.get("author") or "")
            transcribed = str(sheet.get("transcribed_by") or "")
            _t = int(sheet.get("total_ms") or 0)
            if _t > 0:
                total_ms = _t
            _n = len(sheet.get("events") or [])
            if _n > 0:
                events = _n
        except Exception:
            pass  # 个别坏谱不影响候选列表，缺字段就留空
        out.append({
            "index": i + 1,
            "name": s["name"],
            "label": _candidate_label(s["name"], community, transcribed, author),
            "file": s["file"],
            "dir": s["dir"],
            "size": s["size"],
            "is_community": community,
            "total_ms": total_ms,
            "events": events,
            "author": author,
            "transcribed_by": transcribed,
        })
    return out


def _fmt_duration(total_ms) -> str:
    """毫秒 -> "25秒"/"2分49秒"：版本清单里最好用的区分信息（同名谱时长几乎必不同）。"""
    try:
        ms = int(total_ms or 0)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    sec = ms // 1000
    if sec < 60:
        return f"{sec}秒"
    return f"{sec // 60}分{sec % 60}秒"


def _version_same(a: dict, b: dict) -> bool:
    """两份谱是不是同一首（被重复上传/只是换了个署名）？

    判据：时长差 <=1s 且音符数差 <=2 —— 同一份转录被重复投递时这两个数几乎必相等；
    解析不出时长的坏谱退化为"展示名完全相同才算同一首"。
    """
    ta, tb = a.get("total_ms"), b.get("total_ms")
    na, nb = a.get("events"), b.get("events")
    if ta and tb and na and nb:
        try:
            return (abs(int(ta) - int(tb)) <= 1000
                    and abs(int(na) - int(nb)) <= 2)
        except (TypeError, ValueError):
            return False
    if not ta and not tb:
        return str(a.get("name") or "").casefold() == str(b.get("name") or "").casefold()
    return False


def find_song_versions(query: str, dirs: list[str | Path], limit: int = 6,
                       scan_limit: int = 24) -> list[dict]:
    """列出这首歌**真正不同的版本**（重复上传的同一份会被合并成一条）。

    play_song 用它决定"直接弹"还是"问玩家要哪个版本"：
    - 0 个：没这个谱；
    - 1 个：只有一个版本，直接弹（包括"有 5 份文件但都是同一首"的情况）；
    - ≥2 个：真的有多个版本 —— 精确名命中也一样要问（"小星星变奏曲"就同时有
      裸版和署名版，不能替玩家随便挑一个）。

    合并口径见 _version_same；代表成员取 find_song_candidates 的排序第一条
    （自带/示例谱优先于社区转录谱）。
    """
    raw = find_song_candidates(query, dirs, limit=max(1, scan_limit),
                               include_partial=True)
    groups: list[dict] = []
    for c in raw:
        for g in groups:
            if _version_same(g, c):
                g["dup_count"] = int(g.get("dup_count") or 1) + 1
                break
        else:
            item = dict(c)
            item["dup_count"] = 1
            groups.append(item)

    out: list[dict] = []
    for i, g in enumerate(groups[:max(0, limit)]):
        g = dict(g)
        g["index"] = i + 1
        g["duration"] = _fmt_duration(g.get("total_ms"))
        g["label"] = _candidate_label(g["name"], g["is_community"],
                                      g.get("transcribed_by", ""), g.get("author", ""))
        if int(g.get("dup_count") or 1) > 1:
            g["label"] += f"（另有{g['dup_count'] - 1}份重复）"
        out.append(g)
    return out


def _candidate_label(name: str, is_community: bool, transcribed_by: str = "",
                     author: str = "") -> str:
    """候选的展示名：同名版本靠"来源/转录者"后缀区分，玩家念得出来才好选。"""
    tags: list[str] = []
    if is_community:
        tags.append("社区转录")
    if transcribed_by:
        tags.append(f"转录:{transcribed_by}")
    elif author:
        tags.append(f"作者:{author}")
    label = name or "(未命名)"
    if tags:
        label += "（" + "，".join(tags) + "）"
    return label


# 中文序号/阿拉伯数字 -> 整数
_CN_ORDINALS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                "七": 7, "八": 8, "九": 9, "十": 10}
# "随便/都行"这类"你帮我挑"的回答一律按第一个处理
_ANY_CHOICE_WORDS = ("随便", "都行", "都可以", "无所谓", "你选", "你定", "第一个", "第一首")


def _choice_ordinal(choice: str) -> int | None:
    """把回答解析成 1-based 序号：支持 "1"、"第2个"、"第二个"、"版本3"、随便…；
    解析不出返回 None。"""
    s = (choice or "").strip()
    if not s:
        return None
    # 纯数字（可带 个/首/版/吧/了 等尾巴），例如 "2" / "2个" / "2 吧"
    m = re.fullmatch(r"(\d{1,2})\s*(?:个|首|版|版本)?\s*(?:吧|好了|呗|了|\.)?", s)
    if m:
        n = int(m.group(1))
        return n if n > 0 else None
    # "第二个"/"第2个"/"第 3 首" —— 允许前后带别的话，取第一个"第X"
    m = re.search(r"第\s*(\d{1,2}|[一二两三四五六七八九十])\s*(?:个|首|版|版本)?", s)
    if m:
        return _cn_num(m.group(1))
    # "版本3" / "版本 3" / "版本三" / "好，那就版本3" —— 问句里就是这么说的，
    # 2026-11-01 真机事故：玩家回"好，那就版本3"没被认出来，整句进了 LLM。
    m = re.search(r"版本?\s*(\d{1,2}|[一二两三四五六七八九十])", s)
    if m:
        return _cn_num(m.group(1))
    # "3号" / "选3版"（不带"版本"二字的简写）
    m = re.search(r"(\d{1,2}|[一二两三四五六七八九十])\s*(?:号|版)", s)
    if m:
        return _cn_num(m.group(1))
    if any(w in s for w in _ANY_CHOICE_WORDS):
        return 1
    return None


def _cn_num(tok: str) -> int | None:
    """'3' -> 3, '二' -> 2；其余返回 None。"""
    if tok.isdigit():
        n = int(tok)
        return n if n > 0 else None
    return _CN_ORDINALS.get(tok)


# 拆关键词用的分隔符（标点/空格/符号）与常见虚词（"要那个版本""来一下"这类要剥掉）
_KW_SPLIT_RE = re.compile(r"[\s，。！？、,.!?~～:：'\"“”‘’()（）\[\]【】/#\-]+")
_KW_FILLER = "的了那个吧这首就要一下吗啊呀哦呢我你他她它想听还再来好都行选整整句"

def _kw_fragments(text: str) -> list[str]:
    """把回答切成可能命中版本名的片段："要变奏曲那个" -> ["变奏曲"]。"""
    out: list[str] = []
    for raw in _KW_SPLIT_RE.split(str(text or "")):
        frag = raw.strip(_KW_FILLER).strip().casefold()
        if len(frag) >= 2 and frag not in out:   # 单个字太容易误命中，不要
            out.append(frag)
    return out


def resolve_choice(candidates: list[dict], choice: str | None) -> dict | None:
    """从候选里挑出玩家要的那首：先序号（1/2、第2个、第二个、随便），再按名字/label 关键词。

    命中返回候选 dict（含 index/file），没命中返回 None（调用方提示重说）。
    """
    if not candidates or not choice:
        return None
    text = str(choice).strip()
    if not text:
        return None

    n = _choice_ordinal(text)
    if n is not None:
        for c in candidates:
            if c.get("index") == n:
                return c
        return None

    squashed = text.casefold()
    fields = []
    for c in candidates:
        vals = [str(c.get("name") or ""), str(c.get("label") or ""),
                Path(str(c.get("file") or "")).stem]
        fields.append((c, [v.strip().casefold() for v in vals if v.strip()]))

    # 1) 直接念出版本名（"我要小星星变奏曲"）
    for c, vals in fields:
        if any(v and v in squashed for v in vals):
            return c
    # 2) 只念了名字里的一段（"要变奏曲那个"）：用切好的片段再比一轮
    for frag in _kw_fragments(text):
        for c, vals in fields:
            if any(v and frag in v for v in vals):
                return c
    return None


# ---------------------------------------------------------------------------
# 播放线程：改编自 windhide ControlledThread，发键通过注入的 chord_fn 回调。
# ---------------------------------------------------------------------------
@dataclass
class PlayerState:
    status: str = "idle"          # idle / playing / paused / finished / stopped / error
    song_name: str = ""
    index: int = 0
    total: int = 0
    played_ms: int = 0
    total_ms: int = 0
    speed: float = 1.0
    last_error: str = ""
    started_at: float = 0.0


class SongPlayer:
    """异步弹奏：play() 立即返回，后台线程按曲谱时序发键。

    chord_fn(keys: list[str], hold_ms: int) -> None
        由调用方（sky-mcp-server）提供，负责把和弦真正发出去
        （硬件/软件后端统一在那一层处理）。
    """

    def __init__(self, chord_fn, on_finish=None):
        self._chord_fn = chord_fn
        self._on_finish = on_finish
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self._pause.set()           # set = 运行
        self._running = False
        self._lock = threading.Lock()
        self._events: list[NoteEvent] = []
        self._state = PlayerState()
        # 拟人化 / 乐器档位（play() 时按参数覆盖；默认关闭拟人化、按可延音乐器处理，
        # 这样老调用方行为完全不变）
        self._humanize = 0
        self._sustain = True
        self._max_hold_ms = 0

    # -- 状态查询 --
    def state(self) -> dict:
        with self._lock:
            s = self._state
            pct = (s.played_ms / s.total_ms * 100.0) if s.total_ms > 0 else 0.0
            return {
                "status": s.status,
                "song": s.song_name,
                "event_index": s.index,
                "event_total": s.total,
                "played_ms": s.played_ms,
                "total_ms": s.total_ms,
                "progress_pct": round(pct, 1),
                "speed": s.speed,
                "last_error": s.last_error,
            }

    def is_active(self) -> bool:
        with self._lock:
            return self._state.status in ("playing", "paused")

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self._state, k, v)

    # -- 控制 --
    def play(self, sheet: dict, speed: float = 1.0,
             humanize: int = 0, sustain: bool = True, max_hold_ms: int = 0) -> None:
        """开始播放一首曲谱（已在播时先停旧的）。

        humanize：0 关闭（默认）/ 1 轻微 / 2 明显，见 humanize_params()。
        sustain ：该乐器能否长按（钢琴 False，管弦/吹奏 True），见 instrument_profile()。
        """
        self.stop()
        events = list(sheet["events"])
        self._humanize = humanize
        self._sustain = bool(sustain)
        self._max_hold_ms = int(max_hold_ms or 0)
        with self._lock:
            self._events = events
            self._state = PlayerState(
                status="playing",
                song_name=sheet.get("name", ""),
                total=len(events),
                total_ms=int(sheet.get("total_ms", 0)),
                speed=max(0.2, min(speed, 4.0)),
                started_at=time.time(),
            )
        self._running = True
        self._pause.set()
        self._thread = threading.Thread(target=self._run, name="sky-song-player", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._state.status == "playing":
                self._state.status = "paused"
        self._pause.clear()

    def resume(self) -> None:
        with self._lock:
            if self._state.status == "paused":
                self._state.status = "playing"
        self._pause.set()

    def stop(self) -> None:
        if not self._running and not (self._thread and self._thread.is_alive()):
            return
        self._running = False
        self._pause.set()          # 避免卡在暂停等待
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None
        with self._lock:
            if self._state.status in ("playing", "paused"):
                self._state.status = "stopped"

    # -- 播放循环 --
    def _run(self) -> None:
        events = self._events
        hp = humanize_params(self._humanize)
        jit_lo, jit_hi = hp["jitter_ms"]
        br_lo, br_hi = hp["breath_ms"]
        be_lo, be_hi = hp["breath_every"]
        breath_at = random.randint(be_lo, be_hi) if be_hi else 0
        since_breath = 0
        try:
            for i, ev in enumerate(events):
                if not self._running:
                    return
                self._pause.wait()
                if not self._running:
                    return

                speed = max(self._state.speed, 0.01)
                gap = ev.gap_ms / speed
                # hold 已按原速 cap 到 gap；倍速同比缩放，下限 PLAY_MIN_HOLD_MS，
                # 因此缩放后 hold 仍不超过缩放后的 gap，不会拖音重叠。
                hold = max(PLAY_MIN_HOLD_MS, int(round(_hold_ms_for(ev) / speed)))
                # 钢琴这类"按住不延音"的乐器：把每个音压短，长按没有意义还会拖慢
                if not self._sustain:
                    hold = min(hold, int(self._max_hold_ms or 110))
                # 拟人化①：音符间微抖动（只影响这一拍，不会像累计误差那样越走越偏）
                if jit_hi:
                    time.sleep(random.randint(jit_lo, jit_hi) / 1000.0)

                if ev.keys:
                    try:
                        self._chord_fn(ev.keys, hold)
                    except Exception as e:  # 发键失败（如游戏不在前台）：记录并停
                        self._set(status="error", last_error=f"{type(e).__name__}: {e}")
                        self._running = False
                        if self._on_finish:
                            self._safe_finish("error")
                        return

                with self._lock:
                    self._state.index = i + 1
                    self._state.played_ms += ev.gap_ms

                # 拟人化②：每 N 个音呼吸一次（真人不会一口气弹完，句间总有个停顿）
                if breath_at:
                    since_breath += 1
                    if since_breath >= breath_at and i + 1 < len(events):
                        since_breath = 0
                        breath_at = random.randint(be_lo, be_hi)
                        time.sleep(random.randint(br_lo, br_hi) / 1000.0)

                # 等到下一个时刻；hold 已包含在间隔内，剩余时间再 sleep。
                tail = max(0.0, (gap - hold) / 1000.0)
                if tail > 0 and i + 1 < len(events):
                    # 分段 sleep，便于 stop 尽快响应。
                    waited = 0.0
                    while waited < tail and self._running:
                        self._pause.wait()
                        step = min(0.05, tail - waited)
                        time.sleep(step)
                        waited += step

            with self._lock:
                final = "finished" if self._running else "stopped"
                self._state.status = final
            self._running = False
            if self._on_finish and final == "finished":
                self._safe_finish("finished")
        except Exception as e:  # 兜底：线程内任何意外都不应让进程挂死
            self._set(status="error", last_error=f"{type(e).__name__}: {e}")
            self._running = False
            if self._on_finish:
                self._safe_finish("error")

    def _safe_finish(self, status: str) -> None:
        try:
            if self._on_finish:
                self._on_finish(status)
        except Exception:
            pass
