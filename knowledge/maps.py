# -*- coding: utf-8 -*-
"""
sky-with-you 本地知识库 · 地图
只收录稳定、客观、可观察的事实（视觉特征用于场景识别）。
易变或不确定的内容（先祖准确名字、光之翼数量、季节排期）一律留空，不编造。
地图命名采用网易国服（如“暮土”，不是旧写法“墓土”）。
"""

# 每个地图字段：
#   description     客观描述
#   difficulty      难度
#   visual_features 画面上可直接观察到的特征（场景识别用）
#   belongs_to      季节/隐藏图所属的主地图（常驻图为 None）
#   connected       从本地图出发「可通往」的下一站。
#                   通则：除遇境（自身）和伊甸外，所有地图都能回遇境（connected 含遇境）。
#                   例外：伊甸是献祭终点，不能中途退出，connected 为空（结束后自动回遇境）。
#                   推进链：晨岛→云野→雨林→霞谷→暮土→禁阁→暴风眼→伊甸（单向，不保证反向）。
#                   季节图 = 遇境 + 所属主图。
#                   别按"相邻图"理解——2026-10-05 复核时就因这个措辞把单向链误报成"连接不对称"。
#   spirits         先祖（留空，待人工核对后再填）
MAPS = {
    "遇境": {
        "description": "玩家的家园，可通过石拱门通往各地图，有星盘和换装衣柜",
        "difficulty": "—",
        "visual_features": ["环形石拱门", "中央星盘", "换装衣柜", "池塘", "开阔的草地平台"],
        "belongs_to": None,
        "connected": ["晨岛", "云野", "雨林", "霞谷", "暮土", "禁阁"],
        "spirits": [],
    },
    "晨岛": {
        "description": "新手地图，天空王国的起点",
        "difficulty": "简单",
        "visual_features": ["沙漠与岩石", "远山", "飞行教学", "半埋的神庙", "暖黄色调"],
        "belongs_to": None,
        "connected": ["遇境", "云野"],
        "spirits": [],
    },
    "云野": {
        "description": "云海覆盖的广阔地图，有大量浮空岛屿",
        "difficulty": "简单",
        "visual_features": ["绿色草地", "浮空岛屿", "蝴蝶", "圆顶神殿", "三塔", "明亮的云海"],
        "belongs_to": None,
        "connected": ["遇境", "雨林", "圣岛"],
        "spirits": [],
    },
    "雨林": {
        "description": "持续下雨的茂密森林，需要躲避雨水、补充能量",
        "difficulty": "中等",
        "visual_features": ["高大树木", "持续降雨", "发光蘑菇", "避雨亭子", "树屋", "偏暗的绿色调"],
        "belongs_to": None,
        "connected": ["遇境", "霞谷", "风行网道"],
        "spirits": [],
    },
    "霞谷": {
        "description": "日落时分的冰雪峡谷，有滑行赛道和竞技场",
        "difficulty": "中等",
        "visual_features": ["冰雪覆盖的地面", "滑行/滑雪赛道", "落日竞技场", "尖顶城堡",
                            "圆梦村", "暖橙与冰雪并存"],
        "belongs_to": None,
        "connected": ["遇境", "暮土", "圆梦村"],
        "spirits": [],
    },
    "暮土": {
        "description": "阴暗荒漠，有冥龙巡逻，被照到会损失光翼",
        "difficulty": "困难",
        "visual_features": ["暗色荒漠", "黑色水域", "冥龙（巨型巡逻生物）", "沉船",
                            "残破城墙", "灰暗压抑色调"],
        "belongs_to": None,
        "connected": ["遇境", "禁阁"],
        "spirits": [],
    },
    "禁阁": {
        "description": "高耸的多层塔楼，需要乘坐电梯逐层上升",
        "difficulty": "困难",
        "visual_features": ["多层书阁", "升降电梯", "漂浮石台", "室内星空穹顶", "垂落的光帘"],
        "belongs_to": None,
        "connected": ["遇境", "暴风眼", "星漠"],
        "spirits": [],
    },
    "圣岛": {
        "description": "圣岛季的热带海岛地图",
        "difficulty": "中等",
        "visual_features": ["热带海岛", "瀑布", "巨大的中空山体", "海滩", "编钟"],
        "belongs_to": "云野",
        "connected": ["遇境", "云野"],
        "spirits": [],
    },
    "圆梦村": {
        "description": "梦想季的雪村地图",
        "difficulty": "中等",
        "visual_features": ["被雪覆盖的村庄", "溜冰场", "木屋", "索道", "冰瀑"],
        "belongs_to": "霞谷",
        "connected": ["遇境", "霞谷"],
        "spirits": [],
    },
    "星漠": {
        "description": "小王子季的沙漠玫瑰园地图",
        "difficulty": "中等",
        "visual_features": ["广阔沙漠", "玫瑰", "巨大月亮", "城堡", "书本造型平台"],
        "belongs_to": "禁阁",
        "connected": ["遇境", "禁阁"],
        "spirits": [],
    },
    "风行网道": {
        "description": "风行季的空中风道网络",
        "difficulty": "中等",
        "visual_features": ["环形风道", "彩色漂浮岛屿", "风洞", "交织的气流通道"],
        "belongs_to": "雨林",
        "connected": ["遇境", "雨林"],
        "spirits": [],
    },
    "暴风眼": {
        "description": "最终挑战之地，碎石与风暴肆虐；献祭前可主动回遇境，开始献祭后只能前进",
        "difficulty": "极难",
        "visual_features": ["碎石雨", "狂风", "黑暗狭窄的通道", "需要躲避的飞石"],
        "belongs_to": None,
        "connected": ["遇境", "伊甸"],
        "spirits": [],
    },
    "伊甸": {
        "description": "献祭之地，献祭后进入重生之路、自动回到遇境（不可中途退出）",
        "difficulty": "极难",
        "visual_features": ["献祭石像", "星河", "重生之路", "光之翼"],
        "belongs_to": None,
        "connected": [],
        "spirits": [],
    },
}


def get_map(name):
    """获取指定地图信息；别名归一后查询，找不到返回 None。"""
    return MAPS.get(_canonical_map(name))


def get_all_maps():
    return list(MAPS.keys())


def get_connected(name):
    info = MAPS.get(_canonical_map(name))
    return list(info["connected"]) if info else []


def get_visual_features(name):
    info = MAPS.get(_canonical_map(name))
    return list(info["visual_features"]) if info else []


# 常见别名 / 旧写法 → 国服标准名
_MAP_ALIASES = {
    "墓土": "暮土",
    "墓場": "暮土",
    "home": "遇境",
    "圣岛季": "圣岛",
    " Sanctuary": "圣岛",
    "梦想季": "圆梦村",
    "小王子季": "星漠",
    "风行季": "风行网道",          # 国服标准名（2026-10-05 seek 复核补：原先只收错名，说"风行季"检索不到）
    "飞行季": "风行网道",          # 常见误写，兼容保留
    "伊甸之眼": "暴风眼",
}


def _canonical_map(name):
    name = (name or "").strip()
    return _MAP_ALIASES.get(name, name)
