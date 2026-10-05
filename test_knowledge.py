# -*- coding: utf-8 -*-
"""test_knowledge.py — 本地知识库 knowledge/ 的离线校验（不联网、不碰真机）。

两部分：
  A. Schema 校验：字段齐全、视觉特征非空、连接引用有效、命名规范、姿势循环正确
  B. 查询边界：别名归一、命中/不命中、空输入、非法输入
"""
import sys

import knowledge
from knowledge import maps, items, actions

npass = 0
nfail = 0


def check(desc, ok):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    print("  [%s] %s" % ("PASS" if ok else "FAIL", desc))


print("A. Schema 校验")

REQUIRED_MAP_FIELDS = {"description", "difficulty", "visual_features",
                       "belongs_to", "connected", "spirits"}
for name, info in maps.MAPS.items():
    check("地图「%s」必需字段齐全" % name,
          REQUIRED_MAP_FIELDS.issubset(info.keys()))
    check("地图「%s」视觉特征非空（场景识别依赖）" % name,
          isinstance(info["visual_features"], list) and len(info["visual_features"]) > 0)
    check("地图「%s」connected 是列表" % name,
          isinstance(info["connected"], list))
    check("地图「%s」spirits 是列表" % name,
          isinstance(info["spirits"], list))
    check("地图「%s」连接引用的地图都存在（无悬空/拼写错误）" % name,
          all(c in maps.MAPS for c in info["connected"]))
    if info["belongs_to"] is not None:
        check("季节图「%s」belongs_to 指向有效主图" % name,
              info["belongs_to"] in maps.MAPS)

# 命名规范：不允许旧写法“墓土”作为 key
check("地图命名用国服“暮土”（无旧写法“墓土”）",
      "暮土" in maps.MAPS and "墓土" not in maps.MAPS)

# 规则：除遇境（自身）和伊甸外，所有地图都能回遇境（新增地图自动覆盖）
for name in maps.get_all_maps():
    if name in ("遇境", "伊甸"):
        continue
    check("「%s」可回遇境（规则断言）" % name,
          "遇境" in maps.MAPS[name]["connected"])

# 遇境石门只通向 6 张主图（不含暴风眼/伊甸/季节图）
check("遇境石门只通向 6 张主图",
      set(maps.MAPS["遇境"]["connected"]) ==
      {"晨岛", "云野", "雨林", "霞谷", "暮土", "禁阁"})

# 伊甸是唯一不可回遇境的地图（connected 为空）
check("伊甸 connected 为空（献祭终点）", maps.MAPS["伊甸"]["connected"] == [])

# 季节图 <-> 所属主图（双向）
for season, host in [("圣岛", "云野"), ("圆梦村", "霞谷"),
                     ("星漠", "禁阁"), ("风行网道", "雨林")]:
    check("季节图「%s」与「%s」双向" % (season, host),
          host in maps.MAPS[season]["connected"]
          and season in maps.MAPS[host]["connected"])

# 物品
REQUIRED_ITEM_FIELDS = {"type", "acquisition", "usage"}
for name, info in items.ITEMS.items():
    check("物品「%s」字段齐全" % name,
          REQUIRED_ITEM_FIELDS.issubset(info.keys())
          and all(info[f] for f in REQUIRED_ITEM_FIELDS))

# 不允许保留会误导的“假每日任务/季节排期”
check("不包含 get_daily_task 假数据",
      not hasattr(items, "get_daily_task") and not hasattr(items, "DAILY_TASKS"))

# 姿势
check("姿势正好 4 态", set(actions.POSES.keys()) == set(actions.POSE_ORDER)
      and len(actions.POSE_ORDER) == 4)
for i, p in enumerate(actions.POSE_ORDER):
    check("姿势「%s」press_3=%d" % (p, i), actions.POSES[p]["press_3"] == i)
# 循环链闭合
for p in actions.POSE_ORDER:
    check("姿势「%s」next 指向有效态" % p, actions.POSES[p]["next"] in actions.POSES)

# 快捷栏关键格
check("快捷栏第3格=姿势、第5格=回遇境",
      actions.HOTBAR[3]["name"].startswith("姿势")
      and actions.HOTBAR[5]["name"] == "回遇境")

print()
print("B. 查询边界")

check("别名 墓土→暮土 能查到", knowledge.get_map("墓土") is not None)
check("标准名 暮土 能查到", knowledge.get_map("暮土") is not None)
check("不存在的地图返回 None", knowledge.get_map("不存在的地方") is None)
check("别名 季蜡→季节蜡烛", knowledge.get_item("季蜡") is not None)
check("别名 光翼→光之翼", knowledge.get_item("光翼") is not None)
check("不存在物品返回 None", knowledge.get_item("不存在物品") is None)

check("query('我们坐下吧') 命中姿势块",
      any("数字3循环" in s for s in knowledge.query("我们坐下吧")))
check("query('回遇境吧') 命中回遇境流程",
      any("数字5" in s for s in knowledge.query("回遇境吧")))
check("query('这是云野吗') 命中云野地图",
      any("地图·云野" in s for s in knowledge.query("这是云野吗")))
check("query('去墓土吧') 经别名命中暮土",
      any("地图·暮土" in s for s in knowledge.query("去墓土吧")))
check("query('小王子季怎么去') 经别名命中星漠",
      any("地图·星漠" in s for s in knowledge.query("小王子季怎么去")))
check("query('风行季怎么去') 经别名命中风行网道（国服标准名）",
      any("地图·风行网道" in s for s in knowledge.query("风行季怎么去")))
check("query('飞行季在哪') 的常见误写也兼容",
      any("地图·风行网道" in s for s in knowledge.query("飞行季在哪")))
check("别名表收录国服标准名「风行季」",
      maps._MAP_ALIASES.get("风行季") == "风行网道")
check("query('我们来抱抱') 命中抱抱",
      any("互动·抱抱" in s for s in knowledge.query("我们来抱抱")))
check("query('我们拥抱一下') 也命中抱抱",
      any("互动·抱抱" in s for s in knowledge.query("我们拥抱一下")))
check("query('打开好友树看看') 给的是 F 不是 G",
      any("互动·打开好友树" in s and "F" in s and " G" not in s
          for s in knowledge.query("打开好友树看看")))
check("姿势文案无歧义（写明反复按同一个键3）",
      any("反复按同一个键 3" in s for s in knowledge.query("我们坐下吧")))
check("query 空文本返回空列表", knowledge.query("") == [])
check("query 不相关文本返回空列表", knowledge.query("哈哈哈哈") == [])

check("pose_press_count standing→lying = 3",
      knowledge.pose_press_count("standing", "lying") == 3)
check("pose_press_count 同姿势 = 0",
      knowledge.pose_press_count("sitting", "sitting") == 0)
check("pose_press_count lying→sitting = 3（绕一圈）",
      knowledge.pose_press_count("lying", "sitting") == 3)
check("pose_press_count 非法输入返回 None",
      knowledge.pose_press_count("x", "y") is None)

check("core_notes 非空且为列表",
      isinstance(knowledge.core_notes(), list) and len(knowledge.core_notes()) > 0)

print()
print("PASS=%d FAIL=%d" % (npass, nfail))
if nfail:
    print("有 %d 条失败" % nfail)
    sys.exit(1)
print("全部通过")
