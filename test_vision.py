# -*- coding: utf-8 -*-
"""core/vision_client 纯函数离线单测（不联网、不需要 key）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import vision_client as V
from panel_detector import judge_friend_tree_menu as jft

P = F = 0
def check(name, cond):
    global P, F
    if cond: P += 1; print("  [PASS]", name)
    else: F += 1; print("  [FAIL]", name)

print("== strip_json_block ==")
check("纯JSON", V.strip_json_block('{"a":1}') == {"a": 1})
check("带json围栏", V.strip_json_block('```json\n{"pose":"sitting"}\n```') == {"pose": "sitting"})
check("带普通围栏", V.strip_json_block('```\n{"x": true}\n```') == {"x": True})
check("前后有啰嗦文字", V.strip_json_block('好的，结果是：{"pose":"lying"} 希望有帮助') == {"pose": "lying"})
check("非法返回None", V.strip_json_block("我看不出来") is None)
check("空返回None", V.strip_json_block("") is None)

print("== normalize_pose ==")
check("standing", V.normalize_pose("standing") == "standing")
check("站着", V.normalize_pose("站着") == "standing")
check("sitting短语", V.normalize_pose("sitting on a stool") == "sitting")
check("坐在地上", V.normalize_pose("坐在地上") == "sitting")
check("crouch", V.normalize_pose("CROUCHING") == "crouching")
check("蹲下", V.normalize_pose("蹲着/屈膝") == "crouching")
check("lying", V.normalize_pose("lying down") == "lying")
check("躺下", V.normalize_pose("躺下了") == "lying")
check("None", V.normalize_pose(None) is None)
check("乱码", V.normalize_pose("???") is None)

print("== pose_press_delta（站0蹲1坐2躺3循环） ==")
check("站->坐按2", V.pose_press_delta(0, 2) == 2)
check("坐->躺按1", V.pose_press_delta(2, 3) == 1)
check("躺->站绕回按1", V.pose_press_delta(3, 0) == 1)
check("同姿势按0", V.pose_press_delta(2, 2) == 0)
check("蹲->躺按2", V.pose_press_delta(1, 3) == 2)

print("== 关闭/无key 时安全回退 ==")
c_off = V.VisionClient(enabled=False, api_key="x")
check("关闭则不可用", c_off.available is False)
check("关闭时ask直接None", c_off.ask(None, "q") is None)
c_nokey = V.VisionClient(enabled=True, api_key="")
# 清掉可能存在的本机环境/文件影响：强制空 key
c_nokey.api_key = ""
check("启用但无key不可用", c_nokey.available is False)
check("无key时classify回退", c_nokey.classify_pose(None) == (None, False))

print("== judge_friend_tree_menu 好友树菜单提示行 ==")
def _ft(x, y, t): return {"text": t, "x": x, "y": y}
check("基础页 选择+SPACE+退后", jft([_ft(1645,1040,"选择"),_ft(1750,1040,"SPACE"),_ft(1830,1040,"退后")]))
check("选择+SPACE即可", jft([_ft(1645,1040,"选择"),_ft(1750,1040,"SPACE")]))
check("选择+退后即可", jft([_ft(1645,1040,"选择"),_ft(1830,1040,"退后")]))
check("聊天面板无选择不算", not jft([_ft(120,1040,"退后"),_ft(220,1040,"ENTER聊天"),_ft(400,1040,"语音输入")]))
check("选择在屏幕中央不算(位置约束)", not jft([_ft(800,400,"选择"),_ft(900,400,"退后")]))
check("只有选择缺确认键不算", not jft([_ft(1645,1040,"选择")]))
check("空列表不算", not jft([]))

print("=" * 50)
print(f"结果: PASS={P} FAIL={F}")
sys.exit(1 if F else 0)
