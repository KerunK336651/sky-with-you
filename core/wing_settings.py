# -*- coding: utf-8 -*-
"""光翼自动拾取的本地设置（双钥匙：账号有翼 + 明确开启）。

设计目的（2026-10-27）：本项目已开源，「无翼号（永久无翼）误拾光翼」是不可逆事故。
所以自动拾取不能靠单一环境变量决定，必须**两把钥匙同时到位**才生效：

  钥匙① `account_winged`  = 这个号是不是有翼号（默认 False = 当作无翼号处理）
  钥匙② `auto_pickup_wing` = 是否要开启自动拾取（默认 False）

  生效 = 钥匙① and 钥匙②（环境变量 SKY_AUTO_PICKUP_WING 可临时覆盖钥匙②，
  但**覆盖不了钥匙①** —— 没标记有翼号，就算设了 env 也强制关闭并告警。）

设置存在 `user_data/settings.json`（已被 .gitignore 排除，纯本地、不进仓库），
因此每个人 clone 下来都是「有翼=False + 拾取=False」的安全默认值。
"""

import json
import os

USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "user_data")
SETTINGS_FILE = os.path.join(USER_DATA_DIR, "settings.json")

# 安全默认值：当作**无翼号**处理、不自动拾取
DEFAULTS = {
    "account_winged": False,      # 有翼号？（无翼号必须 False）
    "auto_pickup_wing": False,    # 开启自动拾取？
}


def load_settings(path=None) -> dict:
    """读取设置，缺失/损坏一律退回安全默认值（有翼=False、拾取=False）。"""
    p = path or SETTINGS_FILE
    out = dict(DEFAULTS)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data:
                    out[k] = bool(data[k])
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return out


def save_settings(values: dict, path=None) -> dict:
    """合并写入设置（原子写：.tmp → os.replace），返回写后的完整设置。"""
    p = path or SETTINGS_FILE
    cur = load_settings(p)
    cur.update({k: bool(v) for k, v in (values or {}).items() if k in DEFAULTS})
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return cur


def resolve_pickup(env_value, settings=None) -> tuple[bool, str]:
    """纯函数（离线可单测）：决定自动拾取是否生效，并给出原因（直接打进启动日志）。

    env_value: SKY_AUTO_PICKUP_WING 的原始字符串（None/"" 表示没设）
    settings : load_settings() 的结果
    返回 (enabled, reason)
    """
    st = settings if isinstance(settings, dict) else dict(DEFAULTS)
    winged = bool(st.get("account_winged", False))
    want = bool(st.get("auto_pickup_wing", False))
    env = str(env_value or "").strip().lower()

    # 钥匙②：环境变量优先于文件里的开关（方便在 bat / 命令行临时开）
    if env in ("1", "true", "yes", "on"):
        want = True
        src = "环境变量 SKY_AUTO_PICKUP_WING=1"
    elif env in ("0", "false", "no", "off"):
        want = False
        src = "环境变量 SKY_AUTO_PICKUP_WING=0"
    else:
        src = "settings.json"

    if not want:
        if winged:
            return False, f"关闭（{src} 未开启；账号已标记有翼，想拾取就开这个开关）"
        return False, f"关闭（{src} 未开启；无翼号请保持关闭）"

    # 钥匙①：没标记有翼号，谁也开不了 —— 这是开源分发的最后一道保险
    if not winged:
        return False, ("强制关闭：账号未标记为有翼号（settings.json account_winged=false），"
                       "即使 SKY_AUTO_PICKUP_WING=1 也不会按 F 拾取光翼。"
                       "确认是有翼号请运行 setup_wing.bat 改设置")

    return True, "开启（账号已标记有翼 + 已明确开启；靠近光翼自动按 F 拾取）"


def describe(settings=None) -> str:
    """给启动日志用的一行说明。"""
    st = settings if isinstance(settings, dict) else load_settings()
    return ("账号=%s 自动拾取开关=%s"
            % ("有翼" if st.get("account_winged") else "无翼(默认)",
               "开" if st.get("auto_pickup_wing") else "关(默认)"))
