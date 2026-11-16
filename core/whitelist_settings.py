# -*- coding: utf-8 -*-
"""白名单（使用者识别）的本地设置。

历史现状：白名单一直只靠环境变量 `SKY_WHITELIST` / `SKY_WHITELIST_ENABLED` 决定，
没法在统一配置向导里持久化，开源用户改起来不方便。本模块把它存到
`user_data/whitelist_settings.json`（`.gitignore` 已排除 user_data/*，不进仓库）：

  - whitelist_enabled : 开关（默认 True）
  - whitelist         : 玩家昵称列表（默认 ["珂珂"]）

优先级：**环境变量 > json > 默认值**。老的 bat 环境变量用法继续有效，
默认（无文件、无环境变量）仍是“开启 + 珂珂”，行为零破坏。
"""

import json
import os

USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "user_data")
SETTINGS_FILE = os.path.join(USER_DATA_DIR, "whitelist_settings.json")

DEFAULTS = {
    "whitelist_enabled": True,
    "whitelist": ["珂珂"],
}


def parse_names(text) -> list:
    """逗号分隔文本 -> 去空、去重且保持顺序的名单。"""
    seen = set()
    out = []
    for part in str(text or "").split(","):
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _coerce_names(raw) -> list:
    if isinstance(raw, str):
        return parse_names(raw)
    if isinstance(raw, list):
        return parse_names(",".join(str(x) for x in raw))
    return []


def load_settings(path=None) -> dict:
    """读设置；缺失/损坏一律退回默认（开启 + 珂珂）。"""
    p = path or SETTINGS_FILE
    out = {"whitelist_enabled": True, "whitelist": ["珂珂"]}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out["whitelist_enabled"] = bool(data.get("whitelist_enabled", True))
            names = _coerce_names(data.get("whitelist", ["珂珂"]))
            out["whitelist"] = names if names else ["珂珂"]
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return out


def save_settings(values: dict, path=None) -> dict:
    """合并写入（原子写：.tmp → os.replace），返回写后的完整设置。"""
    p = path or SETTINGS_FILE
    cur = load_settings(p)
    if "whitelist_enabled" in (values or {}):
        cur["whitelist_enabled"] = bool(values["whitelist_enabled"])
    if "whitelist" in (values or {}):
        names = _coerce_names(values["whitelist"])
        if names:
            cur["whitelist"] = names
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return cur


def enabled_of(settings=None) -> bool:
    """开关最终值：环境变量 SKY_WHITELIST_ENABLED 优先，否则 json。"""
    env = str(os.environ.get("SKY_WHITELIST_ENABLED", "") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    st = settings if isinstance(settings, dict) else load_settings()
    return bool(st.get("whitelist_enabled", True))


def names_of(settings=None) -> list:
    """名单最终值：环境变量 SKY_WHITELIST 优先（非空才采用），否则 json。"""
    env = str(os.environ.get("SKY_WHITELIST", "") or "").strip()
    if env:
        parsed = parse_names(env)
        if parsed:
            return parsed
    st = settings if isinstance(settings, dict) else load_settings()
    names = _coerce_names(st.get("whitelist", ["珂珂"]))
    return names if names else ["珂珂"]


def describe(settings=None) -> str:
    """给启动日志/向导用的一行说明。"""
    st = settings if isinstance(settings, dict) else load_settings()
    if not enabled_of(st):
        return "白名单=关闭（所有人都能触发，AI 自己判断）"
    return "白名单=开启 | 名单=" + "、".join(names_of(st))
