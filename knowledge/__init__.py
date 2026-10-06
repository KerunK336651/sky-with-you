# -*- coding: utf-8 -*-
"""
sky-with-you 本地知识库
纯本地、零封号风险。提供地图/物品/动作的查询，以及按玩家消息检索相关常识。

用法：
    from knowledge import query, core_notes
    snippets = query("我们坐下吧")      # → 相关知识文本片段
    notes = core_notes()                # → 常驻的操作约定
"""

from . import maps, items, actions


# ── 单条格式化 ──
def _format_map(name, info):
    out = [f"【地图·{name}】{info['description']}（难度：{info['difficulty']}）"]
    if info.get("visual_features"):
        out.append("画面特征：" + "、".join(info["visual_features"]))
    if info.get("belongs_to"):
        out.append(f"属于：{info['belongs_to']}")
    if info.get("enter_from"):
        out.append(f"入口：{info['enter_from']}")
    if info.get("connected"):
        out.append("通往：" + "、".join(info["connected"]))
    areas = info.get("areas") or []
    if areas:
        out.append("区域（共 %d 个）：%s"
                   % (len(areas), "、".join(a["name"] for a in areas)))
    return "\n".join(out)


def _format_item(name, info):
    return (f"【物品·{name}】（{info['type']}）获取：{info['acquisition']}；"
            f"用途：{info['usage']}")


def _format_interaction(name, info):
    key = f"按键{info['key']}，" if info.get("key") else ""
    return f"【互动·{name}】{key}{info['how']}"


# ── 区域名 → 所属地图 ──
_AREA_INDEX = None


def _area_index():
    """区域名 -> 所属地图（懒构建并缓存）。

    只收「不重名、且本身不是地图名/地图别名」的区域名：像「圣岛」「风行网道」
    「云巢」「圆梦村」「暴风眼」既是某张图的区域名、又是另一张图的正式名，
    「伊甸之眼」「星光沙漠」是别名，放进来会互相抢命中，一律排除。
    跨图重名的区域名也直接丢弃（宁可少命中，不要错归属）。
    """
    global _AREA_INDEX
    if _AREA_INDEX is not None:
        return _AREA_INDEX
    reserved = set(maps.MAPS) | set(maps._MAP_ALIASES)
    seen = {}
    dup = set()
    for m in maps.get_all_maps():
        for a in maps.get_areas(m):
            n = a["name"]
            if n in reserved:
                continue
            if n in seen:
                dup.add(n)
            seen[n] = m
    for n in dup:
        seen.pop(n, None)
    _AREA_INDEX = seen
    return _AREA_INDEX


# ── 按玩家消息检索相关常识 ──
def query(text, limit=6):
    """返回与文本相关的知识片段（list[str]）。只匹配名称，避免过度联想。"""
    text = (text or "").strip()
    hits = []
    hit_maps = []

    for m in maps.get_all_maps():
        aliases = [m] + [a for a, std in maps._MAP_ALIASES.items() if std == m]
        if any(a and a in text for a in aliases):
            hit_maps.append(m)
            hits.append(_format_map(m, maps.MAPS[m]))

    # 玩家常直接报区域名（"我在四龙图""秘密花园在哪"）：区域名唯一时归属到所属地图
    for area, m in _area_index().items():
        if area in text and m not in hit_maps:
            hit_maps.append(m)
            hits.append(_format_map(m, maps.MAPS[m])
                        + "\n（你说的「%s」是这张图里的区域）" % area)

    for it in items.get_all_items():
        names = [it] + [a for a, std in items._ITEM_ALIASES.items() if std == it]
        if any(n and n in text for n in names):
            hits.append(_format_item(it, items.ITEMS[it]))

    for xname, xinfo in actions.INTERACTIONS.items():
        extra = ("拥抱",) if xname == "抱抱" else ()
        if xname in text or any(e in text for e in extra):
            hits.append(_format_interaction(xname, xinfo))

    # 姿势类关键词
    if any(k in text for k in ("蹲下", "蹲", "坐下", "坐着", "躺下", "躺", "姿势", "站起来")):
        hits.append(_pose_block())

    # 回遇境（动作流程）
    if "回遇境" in text or "回遇" in text or "回家" in text:
        hits.append("【动作·回遇境】按快捷栏第5格（数字5）→ 弹窗用方向键选择、"
                    "空格确认 → 等待过场，途中不要再按空格。")

    # 每日大蜡烛轮换（稳定周循环；来源与三源不一致的提醒见 maps.py 文件头）
    if any(k in text for k in ("大蜡烛", "大蜡", "烛火位置", "宝藏")):
        hits.append("【大蜡烛·周轮换】" + maps.describe_treasure_rotation()
                    + "。除霞谷是 2 组，其余每天 3 组；每组 4 堆、每堆 50 点烛火。"
                    "（此表尚未实机核对）")

    return hits[:limit]


def _pose_block():
    return ("【姿势·数字3循环】反复按同一个键 3：按1次蹲、按2次坐、按3次躺、按4次回到站立。"
            "牵手或互动后姿势会重置为站立，需要重新摆。")


def core_notes():
    """常驻的操作约定（状态联动），list[str]。"""
    return list(actions.STATE_NOTES)


# ── 直通查询 ──
def get_map(name):
    return maps.get_map(name)


def get_areas(name):
    return maps.get_areas(name)


def get_enter_from(name):
    return maps.get_enter_from(name)


def get_treasure_maps(weekday=None):
    """指定星期出大蜡烛的地图；weekday 同 datetime.weekday()（0=周一）；不传用今天。"""
    return maps.get_treasure_maps(weekday)


def describe_treasure_rotation():
    return maps.describe_treasure_rotation()


def get_item(name):
    return items.get_item(name)


def get_pose(name):
    return actions.get_pose(name)


def pose_press_count(current, target):
    return actions.pose_press_count(current, target)
