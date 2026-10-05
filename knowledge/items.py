# -*- coding: utf-8 -*-
"""
sky-with-you 本地知识库 · 物品与货币
只收录稳定的货币/道具常识。
不做“今日每日任务”“当前季节排期”这类会变且容易答错的内容——
上游 items.py 的 get_daily_task() 用 day%7 硬编码，是错误示范，这里不保留。
"""

# type：货币 / 能力 / 消耗品 / 资源
# acquisition：获取方式；usage：用途
ITEMS = {
    "白蜡烛": {
        "type": "货币",
        "acquisition": "跑图收集烛火、点燃蜡烛堆，每日刷新",
        "usage": "兑换先祖物品、好友互动（牵手、击掌、拥抱等）",
    },
    "爱心": {
        "type": "货币",
        "acquisition": "好友用三根蜡烛赠送、先祖节点兑换",
        "usage": "兑换高级装扮、表情和道具",
    },
    "季节蜡烛": {
        "type": "货币",
        "acquisition": "完成季节任务、每日季节烛火",
        "usage": "在当季先祖处兑换季节物品，季节结束后按比例转换",
    },
    "升华蜡烛": {
        "type": "货币",
        "acquisition": "在伊甸献祭光翼获得",
        "usage": "解锁先祖节点、兑换永久光之翼",
    },
    "光之翼": {
        "type": "能力",
        "acquisition": "各地图收集，升华蜡烛可解锁永久光翼",
        "usage": "增加披风能量和飞行能力，被冥龙撞击或淋雨会损失",
    },
    "季卡": {
        "type": "道具",
        "acquisition": "赛季期间购买",
        "usage": "解锁当季额外兑换节点和奖励",
    },
    "魔法": {
        "type": "消耗品",
        "acquisition": "魔法工坊/商船领取，活动与先祖奖励",
        "usage": "临时效果，如身高变化、回复能量、装扮体验等",
    },
    "蜡烛堆": {
        "type": "资源",
        "acquisition": "各地图固定位置，部分需多人点亮",
        "usage": "点燃后获得烛火",
    },
}


def get_item(name):
    return ITEMS.get(_canonical_item(name))


def get_all_items():
    return list(ITEMS.keys())


_ITEM_ALIASES = {
    "蜡烛": "白蜡烛",
    "季蜡": "季节蜡烛",
    "红蜡烛": "升华蜡烛",
    "升华": "升华蜡烛",
    "光翼": "光之翼",
    "翅膀": "光之翼",
    "季节卡": "季卡",
    "礼遇": "季卡",
}


def _canonical_item(name):
    name = (name or "").strip()
    return _ITEM_ALIASES.get(name, name)
