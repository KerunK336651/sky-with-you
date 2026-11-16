# -*- coding: utf-8 -*-
"""弹琴相关的本地设置（快捷栏槽位 / 乐器类型）。

为什么单独一个文件：`user_data/settings.json` 是光翼拾取在用的（`core/wing_settings.py`），
那边的 save 只保留自己认识的键 —— 两个模块共用同一个文件会互相把对方的键写没。
所以弹琴设置单独放 `user_data/song_settings.json`（`.gitignore` 已排除 user_data/*，不进仓库）。

为什么槽位必须可配：每台电脑、每个号的 1~8 快捷栏摆位都不一样，
开源给别人用时更不能写死。槽位由 `setup_song.py` 向导记录，播放前还会用 YOLO 复核。
"""

import json
import os

USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "user_data")
SETTINGS_FILE = os.path.join(USER_DATA_DIR, "song_settings.json")

# 乐器档位：钢琴按住不长音，小提琴/笛子/口琴可以拉长（见 sky_song_player.INSTRUMENT_PROFILES）
INSTRUMENT_KINDS = ("piano", "violin", "flute", "harmonica")
KIND_LABELS = {
    "piano": "钢琴",
    "violin": "小提琴",
    "flute": "笛子",
    "harmonica": "口琴",
}

# 安全默认值：槽位未知（不猜）、按钢琴处理（不能长按，最保守）
DEFAULTS = {
    "instrument_slot": 0,        # 1~8；0 = 还没配过
    "instrument_kind": "piano",  # 见 INSTRUMENT_KINDS
}


def load_settings(path=None) -> dict:
    """读设置；缺失/损坏一律退回安全默认值（槽位未知、钢琴）。"""
    p = path or SETTINGS_FILE
    out = dict(DEFAULTS)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            slot = data.get("instrument_slot", 0)
            try:
                slot = int(slot)
            except Exception:
                slot = 0
            out["instrument_slot"] = slot if 1 <= slot <= 8 else 0
            kind = str(data.get("instrument_kind", "") or "").strip().lower()
            if kind in INSTRUMENT_KINDS:
                out["instrument_kind"] = kind
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return out


def save_settings(values: dict, path=None) -> dict:
    """合并写入（原子写：.tmp → os.replace），返回写后的完整设置。"""
    p = path or SETTINGS_FILE
    cur = load_settings(p)
    for k in DEFAULTS:
        if k in (values or {}):
            cur[k] = values[k]
    cur = _validate(cur)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return cur


def _validate(values: dict) -> dict:
    out = dict(DEFAULTS)
    try:
        slot = int((values or {}).get("instrument_slot", 0))
    except Exception:
        slot = 0
    out["instrument_slot"] = slot if 1 <= slot <= 8 else 0
    kind = str((values or {}).get("instrument_kind", "") or "").strip().lower()
    out["instrument_kind"] = kind if kind in INSTRUMENT_KINDS else "piano"
    return out


def slot_of(settings=None) -> int:
    """当前槽位（0 = 未知）。环境变量 SKY_INSTRUMENT_SLOT 可临时覆盖，便于调试。"""
    env = str(os.environ.get("SKY_INSTRUMENT_SLOT", "") or "").strip()
    if env:
        try:
            v = int(env)
            if 1 <= v <= 8:
                return v
        except Exception:
            pass
    st = settings if isinstance(settings, dict) else load_settings()
    try:
        v = int(st.get("instrument_slot", 0))
    except Exception:
        return 0
    return v if 1 <= v <= 8 else 0


def slot_key(settings=None) -> str | None:
    """槽位对应的按键名（"1".."8"）；未配置返回 None —— 调用方不许瞎猜。"""
    s = slot_of(settings)
    return str(s) if s else None


def kind_of(settings=None) -> str:
    """乐器类型；环境变量 SKY_INSTRUMENT 优先。非法一律钢琴。"""
    env = str(os.environ.get("SKY_INSTRUMENT", "") or "").strip().lower()
    if env in INSTRUMENT_KINDS:
        return env
    st = settings if isinstance(settings, dict) else load_settings()
    kind = str(st.get("instrument_kind", "") or "").strip().lower()
    return kind if kind in INSTRUMENT_KINDS else "piano"


def describe(settings=None) -> str:
    """给启动日志用的一行说明。"""
    st = settings if isinstance(settings, dict) else load_settings()
    slot = slot_of(st)
    kind = kind_of(st)
    slot_txt = f"快捷栏 {slot}" if slot else "快捷栏未设置（跑 setup_song.bat 配）"
    return f"乐器={KIND_LABELS.get(kind, kind)} | {slot_txt}"
