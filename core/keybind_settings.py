# -*- coding: utf-8 -*-
"""快捷栏动作键位的本地设置（点火/收火、鞠躬、回遇境、姿势循环键）。

为什么单独一个文件：和 `wing_settings.py` / `song_settings.py` 一样，每个模块的 save
只保留自己认识的键，共用同一个 json 会互相把对方的键写没。所以键位单独存
`user_data/keybind_settings.json`（`.gitignore` 已排除 user_data/*，不进仓库）。

为什么槽位必须可配：每台电脑、每个号的 1~8 快捷栏摆位都不一样（有翼号/无翼号尤其不同），
开源给别人用时更不能写死。

默认值策略（和 song_settings 不同）：弹琴槽位未知时用 0=不猜（必须先配），
但这些常用动作键历史上一直写死在 1/2/3/5，为了**零破坏**，这里默认值直接取现状——
就算没有 json 文件、没跑过向导，行为也和改造前完全一致；想改再跑向导或写 json。
"""

import json
import os

USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "user_data")
SETTINGS_FILE = os.path.join(USER_DATA_DIR, "keybind_settings.json")

# 槽位 -> （中文含义，对应的环境变量名）
SLOT_META = {
    "fire_slot":   ("点火/收火", "SKY_FIRE_SLOT"),
    "bow_slot":    ("鞠躬",       "SKY_BOW_SLOT"),
    "gohome_slot": ("回遇境",     "SKY_GOHOME_SLOT"),
    "pose_slot":   ("姿势(蹲/坐/躺)", "SKY_POSE_SLOT"),
}

# 默认值：等于改造前写死的键位，保证不配置也照常工作
DEFAULTS = {
    "fire_slot": 1,    # 点火/收火（同一个键，按一下点火、再按收火）
    "bow_slot": 2,     # 鞠躬
    "gohome_slot": 5,  # 回遇境
    "pose_slot": 3,    # 蹲/坐/躺循环键
}


def _valid_slot(value, default):
    """槽位必须是 1~8；非法一律回退默认值。"""
    try:
        v = int(value)
    except Exception:
        return default
    return v if 1 <= v <= 8 else default


def load_settings(path=None) -> dict:
    """读设置；缺失/损坏一律退回默认键位（1/2/5/3）。"""
    p = path or SETTINGS_FILE
    out = dict(DEFAULTS)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, default in DEFAULTS.items():
                if k in data:
                    out[k] = _valid_slot(data.get(k), default)
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
            cur[k] = _valid_slot(values[k], cur[k])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return cur


def _resolve_slot(key: str, settings=None) -> int:
    """单个槽位的最终值：环境变量优先（调试用），其次 json，最后默认值。"""
    _, env_name = SLOT_META[key]
    default = DEFAULTS[key]
    env = str(os.environ.get(env_name, "") or "").strip()
    if env:
        env_v = _valid_slot(env, 0)
        if env_v:
            return env_v
    st = settings if isinstance(settings, dict) else load_settings()
    return _valid_slot(st.get(key, default), default)


def fire_slot(settings=None) -> int:
    return _resolve_slot("fire_slot", settings)


def bow_slot(settings=None) -> int:
    return _resolve_slot("bow_slot", settings)


def gohome_slot(settings=None) -> int:
    return _resolve_slot("gohome_slot", settings)


def pose_slot(settings=None) -> int:
    return _resolve_slot("pose_slot", settings)


def act_keys(settings=None) -> dict:
    """构建给主程序用的动作键字典（点火/收火共用一个槽位）。"""
    st = settings if isinstance(settings, dict) else load_settings()
    fire = str(fire_slot(st))
    return {
        '点火': fire,
        '收火': fire,
        '鞠躬': str(bow_slot(st)),
        '回遇境': str(gohome_slot(st)),
    }


def pose_key(settings=None) -> str:
    """姿势循环键的按键名（"1".."8"）。"""
    return str(pose_slot(settings))


def describe(settings=None) -> str:
    """给启动日志用的一行说明。"""
    st = settings if isinstance(settings, dict) else load_settings()
    parts = []
    for key, (label, _env) in SLOT_META.items():
        parts.append(f"{label}={_resolve_slot(key, st)}")
    return "键位 | " + " | ".join(parts)
