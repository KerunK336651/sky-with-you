# -*- coding: utf-8 -*-
"""
sky-with-you 本地知识库 · 地图
只收录稳定、客观、可观察的事实（视觉特征用于场景识别）。
易变或不确定的内容（先祖准确名字、光之翼数量、季节排期）一律留空，不编造。
地图命名采用网易国服（如“暮土”，不是旧写法“墓土”）。

每个地图字段：
  description     客观描述
  difficulty      难度
  visual_features 画面上可直接观察到的特征（场景识别用）
  belongs_to      季节/隐藏图所属的主地图（常驻图为 None）
  connected       从本地图出发「可通往」的下一站。
                  通则：除遇境（自身）和伊甸外，所有地图都能回遇境（connected 含遇境）。
                  例外：伊甸是献祭终点，不能中途退出，connected 为空（结束后自动回遇境）。
                  推进链：晨岛→云野→雨林→霞谷→暮土→禁阁→暴风眼→伊甸（单向，不保证反向）。
                  季节图 = 遇境 + 所属主图。
                  别按"相邻图"理解——2026-10-05 复核时就因这个措辞把单向链误报成"连接不对称"。
  spirits         先祖（留空，待人工核对后再填）
  enter_from      进入本地图的入口（季节图/隐藏图用）
  areas           本地图内部的独立区域（list[dict]）

areas 每一项：
  name      区域名（国服叫法）
  from      从哪进（上一站）
  barrier   进入条件（可选）。写法沿用来源原样：「2 晨岛 + 3 云野」= 需要
            2 个晨岛先祖 + 3 个云野先祖；「双人」= 需要两个人；
            「20 个光之翼」= 光翼数量条件；「门扉道具」= 需要该道具。
  no_fly    True 表示该区域禁飞（可选，缺省表示可飞）
  season    该区域所属季节（可选，常驻区域无此项）
  note      其他稳定提示（可选）

数据来源与时效（2026-10-06 DSH 核对，详见 PROJECT_HANDOFF.md 第二十八章）：
  areas / enter_from 取自 SkyAuto 0.1.12 的 Assets/RunMap/maps.json（逐个区域抄录其
  客观字段，未收录其烛火量/光翼数量——那两类随版本变动，按本文件既定原则不收）。
  TREASURE_ROTATION 每日大蜡烛轮换表有两个独立来源互相印证：SkyAuto maps.json 的
  treasureChina，以及 TapTap 攻略「每日任务规律之大蜡烛篇」（明确写"大蜡烛的位置
  每周相同的日子都是一样的"）。但 BWIKI「光遇烛光」页的描述与此不同（称每日在
  云野/雨林/霞谷随机 1-2 组、禁阁暮土圣岛晨岛试炼为常驻），三源不一致，
  因此该表**尚未实机核对**，用于真实跑图前请先自己确认一次。
"""
MAPS = {
    "遇境": {
        "description": "玩家的家园，可通过石拱门通往各地图，有星盘和换装衣柜",
        "difficulty": "—",
        "visual_features": ["环形石拱门", "中央星盘", "换装衣柜", "池塘", "开阔的草地平台"],
        "belongs_to": None,
        "connected": ["晨岛", "云野", "雨林", "霞谷", "暮土", "禁阁", "云巢"],
        "spirits": [],
    },
    "晨岛": {
        "description": "新手地图，天空王国的起点",
        "difficulty": "简单",
        "visual_features": ["沙漠与岩石", "远山", "飞行教学", "半埋的神庙", "暖黄色调"],
        "belongs_to": None,
        "connected": ["遇境", "云野"],
        "spirits": [],
        "areas": [
            {"name": "远古码头", "from": "传送门"},
            {"name": "晨岛神殿", "from": "巨型石阶顺风飞"},
            {"name": "观云台", "from": "神殿右侧云隧道", "barrier": "双人", "season": "归属季"},
            {"name": "历练之石", "from": "沙漠中央冥想圈", "season": "夜行季"},
            {"name": "迁徙营地", "from": "沙漠左侧", "season": "迁徙季"},
            {"name": "预言山谷", "from": "沙漠右侧云层下", "barrier": "2 雨林", "season": "预言季"},
            {"name": "水之试炼", "from": "预言山谷", "no_fly": True},
            {"name": "土之试炼", "from": "预言山谷", "no_fly": True},
            {"name": "风之试炼", "from": "预言山谷", "no_fly": True},
            {"name": "火之试炼", "from": "预言山谷", "no_fly": True},
        ],
    },
    "云野": {
        "description": "云海覆盖的广阔地图，有大量浮空岛屿",
        "difficulty": "简单",
        "visual_features": ["绿色草地", "浮空岛屿", "蝴蝶", "圆顶神殿", "三塔", "明亮的云海"],
        "belongs_to": None,
        "connected": ["遇境", "雨林", "圣岛"],
        "spirits": [],
        "areas": [
            {"name": "云野大厅", "from": "传送门"},
            {"name": "蝴蝶平原", "from": "云野大厅", "barrier": "1 云野（右侧小洞）"},
            {"name": "仙乡", "from": "蝴蝶平原正前方", "barrier": "双人门"},
            {"name": "云野神殿", "from": "仙乡乘光鳐"},
            {"name": "幽光山洞", "from": "蝴蝶平原左门", "barrier": "2 晨岛 + 3 云野"},
            {"name": "云峰", "from": "幽光山洞坐船", "barrier": "1 雨林", "season": "拾光季"},
            {"name": "中央神坛", "from": "仙乡右岛山洞", "barrier": "6 云野（电梯要 8 人）"},
            {"name": "云顶浮石", "from": "蝴蝶平原右门", "barrier": "4 云野"},
            {"name": "圣岛", "from": "云顶浮石里侧云洞", "season": "圣岛季"},
        ],
    },
    "雨林": {
        "description": "持续下雨的茂密森林，需要躲避雨水、补充能量",
        "difficulty": "中等",
        "visual_features": ["高大树木", "持续降雨", "发光蘑菇", "避雨亭子", "树屋", "偏暗的绿色调"],
        "belongs_to": None,
        "connected": ["遇境", "霞谷", "风行网道"],
        "spirits": [],
        "areas": [
            {"name": "雨林大厅", "from": "传送门"},
            {"name": "静谧庭院", "from": "雨林大厅"},
            {"name": "荧光森林", "from": "静谧庭院"},
            {"name": "密林遗迹", "from": "荧光森林"},
            {"name": "雨林神殿", "from": "密林遗迹"},
            {"name": "栖光雨潭", "from": "神殿后门"},
            {"name": "风行网道", "from": "雨林大厅右上", "barrier": "1 禁阁", "season": "风行季"},
            {"name": "大树屋", "from": "雨林大厅小门", "barrier": "1 霞谷", "season": "集结季"},
            {"name": "秘密花园", "from": "荧光森林", "barrier": "8 雨林"},
            {"name": "地下溶洞", "from": "秘密花园树洞", "barrier": "双人", "season": "归属季"},
            {"name": "青鸟剧场", "from": "秘密花园冥想圈", "season": "青鸟季"},
        ],
    },
    "霞谷": {
        "description": "日落时分的冰雪峡谷，有滑行赛道和竞技场",
        "difficulty": "中等",
        "visual_features": ["冰雪覆盖的地面", "滑行/滑雪赛道", "落日竞技场", "尖顶城堡",
                            "圆梦村", "暖橙与冰雪并存"],
        "belongs_to": None,
        "connected": ["遇境", "暮土", "圆梦村"],
        "spirits": [],
        "areas": [
            {"name": "霞谷大厅", "from": "传送门"},
            {"name": "滑冰场", "from": "霞谷大厅"},
            {"name": "霞光城", "from": "滑冰场", "barrier": "2 霞谷"},
            {"name": "飞行赛道", "from": "霞光城"},
            {"name": "滑行赛道", "from": "滑冰场"},
            {"name": "落日竞技场", "from": "两条赛道终点"},
            {"name": "霞谷神殿", "from": "竞技场大门"},
            {"name": "圆梦村", "from": "霞谷大厅隧道", "barrier": "1 暮土", "season": "梦想季"},
            {"name": "音乐大厅", "from": "圆梦村", "season": "表演季"},
            {"name": "圆梦村剧场", "from": "圆梦村", "season": "表演季"},
            {"name": "雪隐峰", "from": "圆梦村缆车", "season": "梦想季"},
        ],
    },
    "暮土": {
        "description": "阴暗荒漠，有冥龙巡逻，被照到会损失光翼",
        "difficulty": "困难",
        "visual_features": ["暗色荒漠", "黑色水域", "冥龙（巨型巡逻生物）", "沉船",
                            "残破城墙", "灰暗压抑色调"],
        "belongs_to": None,
        "connected": ["遇境", "禁阁"],
        "spirits": [],
        "areas": [
            {"name": "暮土大厅", "from": "传送门"},
            {"name": "边陲荒漠", "from": "暮土大厅"},
            {"name": "一龙图", "from": "边陲荒漠"},
            {"name": "四龙图", "from": "一龙图"},
            {"name": "远古战场", "from": "四龙图"},
            {"name": "暮土神殿", "from": "远古战场大门"},
            {"name": "黑水港湾", "from": "四龙图管道", "barrier": "1-2 暮土"},
            {"name": "失落方舟", "from": "边陲荒漠坐船", "season": "魔法季"},
            {"name": "藏宝岛礁", "from": "暮土大厅坐船", "barrier": "要走到过禁阁",
             "season": "潜海季", "note": "要潜水"},
            {"name": "往昔暮土", "from": "云巢或禁阁的长老灯笼", "season": "双星季",
             "note": "叫法、归属待确认"},
        ],
    },
    "禁阁": {
        "description": "高耸的多层塔楼，需要乘坐电梯逐层上升",
        "difficulty": "困难",
        "visual_features": ["多层书阁", "升降电梯", "漂浮石台", "室内星空穹顶", "垂落的光帘"],
        "belongs_to": None,
        "connected": ["遇境", "暴风眼", "星漠"],
        "spirits": [],
        "areas": [
            {"name": "一、二层", "from": "传送门", "barrier": "一楼 4 人墙 + 二楼 4 人门"},
            {"name": "三层", "from": "电梯"},
            {"name": "四、五层", "from": "电梯", "note": "四楼全天掉烛火"},
            {"name": "顶层神殿", "from": "电梯"},
            {"name": "档案阁", "from": "一层右侧", "barrier": "双人 + 1 禁阁", "season": "归属季"},
            {"name": "庇护所", "from": "一层左侧楼梯", "barrier": "1 禁阁", "season": "追忆季"},
            {"name": "织光阁", "from": "一层电梯后墙", "barrier": "1 禁阁", "season": "织光季"},
            {"name": "秘密基地", "from": "一层左侧隐藏入口", "barrier": "合伙人斗篷（活动时全开）"},
            {"name": "故事空间", "from": "一层左侧", "barrier": "1 禁阁",
             "note": "原藏星阁，国服叫法待确认"},
            {"name": "星光沙漠", "from": "故事空间", "season": "小王子季"},
            {"name": "月牙绿洲", "from": "故事空间", "season": "九色鹿季"},
            {"name": "姆明谷", "from": "故事空间", "season": "姆明季"},
            {"name": "星夜画廊", "from": "故事空间", "season": "致梵高", "note": "官方叫法未查到"},
        ],
    },
    "云巢": {
        "description": "第二个家园，有班车通往各季节区域，也有商店和日常点位",
        "difficulty": "—",
        "visual_features": ["石块铺成的村落广场", "彩色小屋", "中央大树", "班车站台", "天空色调明亮"],
        "belongs_to": None,
        "connected": ["遇境"],
        "spirits": [],
        "areas": [
            {"name": "云巢", "from": "遇境的云巢门"},
            {"name": "甜点工坊", "from": "云巢", "note": "每天 50 烛火的点心"},
            {"name": "狂欢船队", "from": "伊甸门前坐小船", "season": "狂欢季"},
            {"name": "音乐餐厅", "from": "坐小船", "season": "二重奏季"},
            {"name": "群星影院", "from": "云巢", "note": "有去往昔暮土的长老灯笼"},
            {"name": "仙境茶会", "from": "门扉", "barrier": "门扉道具"},
        ],
    },
    "圣岛": {
        "description": "圣岛季的热带海岛地图",
        "difficulty": "中等",
        "visual_features": ["热带海岛", "瀑布", "巨大的中空山体", "海滩", "编钟"],
        "belongs_to": "云野",
        "connected": ["遇境", "云野"],
        "spirits": [],
        "enter_from": "云野·云顶浮石里侧云洞",
    },
    "圆梦村": {
        "description": "梦想季的雪村地图",
        "difficulty": "中等",
        "visual_features": ["被雪覆盖的村庄", "溜冰场", "木屋", "索道", "冰瀑"],
        "belongs_to": "霞谷",
        "connected": ["遇境", "霞谷"],
        "spirits": [],
        "enter_from": "霞谷大厅隧道",
    },
    "星漠": {
        "description": "小王子季的沙漠玫瑰园地图",
        "difficulty": "中等",
        "visual_features": ["广阔沙漠", "玫瑰", "巨大月亮", "城堡", "书本造型平台"],
        "belongs_to": "禁阁",
        "connected": ["遇境", "禁阁"],
        "spirits": [],
        "enter_from": "禁阁·故事空间",
    },
    "风行网道": {
        "description": "风行季的空中风道网络",
        "difficulty": "中等",
        "visual_features": ["环形风道", "彩色漂浮岛屿", "风洞", "交织的气流通道"],
        "belongs_to": "雨林",
        "connected": ["遇境", "雨林"],
        "spirits": [],
        "enter_from": "雨林大厅右上",
        "barrier": "1 禁阁",
    },
    "暴风眼": {
        "description": "最终挑战之地，碎石与风暴肆虐；献祭前可主动回遇境，开始献祭后只能前进",
        "difficulty": "极难",
        "visual_features": ["碎石雨", "狂风", "黑暗狭窄的通道", "需要躲避的飞石"],
        "belongs_to": None,
        "connected": ["遇境", "伊甸"],
        "spirits": [],
        "areas": [
            {"name": "伊甸大门", "from": "禁阁神殿后面", "barrier": "20 个光之翼"},
            {"name": "暴风眼", "from": "伊甸大门",
             "note": "风大基本飞不了；走到最后不能回头"},
        ],
    },
    "伊甸": {
        "description": "献祭之地，献祭后进入重生之路、自动回到遇境（不可中途退出）",
        "difficulty": "极难",
        "visual_features": ["献祭石像", "星河", "重生之路", "光之翼"],
        "belongs_to": None,
        "connected": [],
        "spirits": [],
        "areas": [
            {"name": "伊甸之眼", "from": "暴风眼", "no_fly": True,
             "note": "献祭：63 座石像，每周日 0 点重置"},
            {"name": "重生之路", "from": "献祭后自动进入", "note": "结束后在家（遇境）重生"},
        ],
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


def get_areas(name):
    """该地图内部的区域列表（list[dict]）；无区域数据或地图不存在时返回空列表。"""
    info = MAPS.get(_canonical_map(name))
    return list(info.get("areas", [])) if info else []


def get_enter_from(name):
    """进入该地图的入口（str）；未收录时返回空字符串。"""
    info = MAPS.get(_canonical_map(name))
    return (info.get("enter_from") or "") if info else ""


# 每日大蜡烛轮换表（国服）。键为 datetime.weekday()：0=周一 … 6=周日。
# 除霞谷是 2 组，其余地图每天 3 组；每组 4 堆、每堆 50 点烛火。
# 来源见文件头「数据来源与时效」——三源未完全一致，尚未实机核对。
TREASURE_ROTATION = {
    0: ["霞谷"],
    1: ["暮土"],
    2: ["禁阁"],
    3: ["云野"],
    4: ["云野", "雨林", "霞谷"],
    5: ["云野", "雨林", "暮土"],
    6: ["雨林", "暮土", "禁阁"],
}

_WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def get_treasure_maps(weekday=None):
    """返回指定星期出大蜡烛的地图列表（list[str]）。

    weekday 同 datetime.weekday()：0=周一 … 6=周日；不传则用本机今天。
    传入 None 以外的非法值（非整数/超范围）时按 7 取模，不抛异常。
    """
    if weekday is None:
        import datetime
        weekday = datetime.date.today().weekday()
    try:
        weekday = int(weekday) % 7
    except (TypeError, ValueError):
        return []
    return list(TREASURE_ROTATION.get(weekday, []))


def describe_treasure_rotation():
    """把整周的大蜡烛表格式化成一行文本（确定性，便于测试与喂给模型）。"""
    parts = []
    for i, name in enumerate(_WEEKDAY_NAMES):
        maps_today = TREASURE_ROTATION.get(i, [])
        parts.append("%s %s" % (name, "、".join(maps_today) if maps_today else "无"))
    return "；".join(parts)


# 常见别名 / 旧写法 → 国服标准名
_MAP_ALIASES = {
    "墓土": "暮土",
    "墓場": "暮土",
    "home": "遇境",
    "圣岛季": "圣岛",
    " Sanctuary": "圣岛",
    "梦想季": "圆梦村",
    "小王子季": "星漠",
    "星光沙漠": "星漠",            # 国服也叫星光沙漠（2026-10-06 DSH 补：SkyAuto maps.json 用此名）
    "风行季": "风行网道",          # 国服标准名（2026-10-05 seek 复核补：原先只收错名，说"风行季"检索不到）
    "飞行季": "风行网道",          # 常见误写，兼容保留
    "伊甸之眼": "伊甸",            # 国服官方把整个伊甸叫「伊甸之眼」（BWIKI 侧栏即为该名）；
                                   # 2026-10-06 DSH 修正：原先错指暴风眼，而暴风眼只是伊甸大门之后的一段
}


def _canonical_map(name):
    name = (name or "").strip()
    return _MAP_ALIASES.get(name, name)
