# -*- coding: utf-8 -*-
"""
test_logic.py — 纯逻辑单元测试（不需要游戏 / Arduino / LLM / 联网）

覆盖 sky-loop-v7.py 里和“识别->解析->去重->过滤”相关的纯函数：
  fix / ocr_str / extract / _is_self_msg / _msg_similar / _seen_before /
  fix_vocab / ChatVoteBuffer / 白名单过滤。
原则：既测“该改的要改”，也测“不该改的绝不能改”（防误伤回归）。

用法:  py test_logic.py
"""
import os, sys, importlib.util
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location(
    "skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  [PASS]", name)
    else: FAIL += 1; print("  [FAIL]", name, "->", detail)

def eq(name, got, exp):
    check(name, got == exp, f"got={got!r} exp={exp!r}")

print("="*60); print("一、fix 错字/漏字修正"); print("="*60)
eq("莪→我", M.fix("莪们走"), "我们走")
eq("行首河-→星河-", M.fix("河-你好"), "星河-你好")
eq("河你好呀→星河你好呀", M.fix("河你好呀"), "星河你好呀")
eq("们又见面了补我", M.fix("们又见面了"), "我们又见面了")
eq("正常句不乱改", M.fix("今天天气真好"), "今天天气真好")
eq("宿啥→宿舍(词组定向)", M.fix("有呀宿啥环境不好"), "有呀宿舍环境不好")
eq("啥友→舍友", M.fix("啥友之间的矛盾"), "舍友之间的矛盾")
eq("华竟→毕竟", M.fix("华竟我不是本科"), "毕竟我不是本科")
eq("反例 干啥的啥不动", M.fix("你想干啥"), "你想干啥")
eq("认对的宿舍不被改坏(防自伤)", M.fix("宿舍的人不讲卫生"), "宿舍的人不讲卫生")

print("="*60); print("二、ocr_str 过滤链"); print("="*60)
items = [
    {"text": " 珂珂-你好 ", "confidence": 0.9},
    {"text": "低置信", "confidence": 0.30},
    {"text": "刚好0.36保留", "confidence": 0.36},
    {"text": "陌生人", "confidence": 0.95},
    {"text": "……", "confidence": 0.95},
    {"text": "莪来了", "confidence": 0.9},
]
out = M.ocr_str(items)
lines = out.split("\n")
check("正常行保留", "珂珂-你好" in lines)
check("conf<=0.35 丢弃(0.30)", "低置信" not in out)
check("边界0.36保留", "刚好0.36保留" in lines)
check("陌生人行丢弃", "陌生人" not in out)
check("纯省略号丢弃", "……" not in out)
check("fix链生效(莪→我)", "我来了" in lines)
eq("空输入", M.ocr_str([]), "")

print("="*60); print("三、extract 名字-内容解析"); print("="*60)
eq("标准 内容-名字", M.extract("你好呀-珂珂"), ["[珂珂] 你好呀"])
multi = M.extract("在吗-珂珂\n在的-星河")
eq("多行", multi, ["[珂珂] 在吗", "[星河] 在的"])
# 名字单独成行：先内容（无分隔符）pending，再一行“-名字”
split = M.extract("一起跑图吗\n-珂珂")
eq("名字换行拼接", split, ["[珂珂] 一起跑图吗"])
ui = M.extract("聊天\nESC\n123\n你好-珂珂")
eq("UI词/纯数字丢弃", ui, ["[珂珂] 你好"])
dots = M.extract("……-珂珂")
eq("纯点号内容丢弃", dots, [])
# 自己刚发过的内容，认领成 [星河] 防自循环
own = M.extract("我刚发的一句话", own_texts=("我刚发的一句话",))
eq("自己消息认领[星河]", own, ["[星河] 我刚发的一句话"])
# 长破折号/波浪线分隔符也认
eq("长破折号分隔", M.extract("等我一下——珂珂"), ["[珂珂] 等我一下"])

print("="*60); print("四、_is_self_msg / _msg_similar"); print("="*60)
check("[星河]是自己", M._is_self_msg("[星河] 嗯") is True)
check("[珂珂]不是自己", M._is_self_msg("[珂珂] 嗯") is False)
check("无括号不是自己", M._is_self_msg("星河嗯") is False)
check("短消息全等才相似(你好=你好)", M._msg_similar("你好", "你好") is True)
check("短消息差字不算(你好vs你号)", M._msg_similar("你好", "你号") is False)
check("忽略名字前缀和标点",
      M._msg_similar("[珂珂] 你在哪里呀", "你在哪里呀。") is True)
check("差异大不算相似",
      M._msg_similar("我们去跑图吧", "今天吃什么呢") is False)

print("="*60); print("五、_seen_before 120秒时序去重"); print("="*60)
st = M.SharedState()
check("空历史没见过", M._seen_before(st, "[珂珂] 你好", 1000.0) is False)
st.recent_seen.append(("[珂珂] 你好", 1000.0))
check("刚见过判重", M._seen_before(st, "[珂珂] 你好", 1001.0) is True)
check("120秒外过期不算", M._seen_before(st, "[珂珂] 你好", 1130.0) is False)

print("="*60); print("六、fix_vocab 保守纠错回归"); print("="*60)
eq("暴风眼错1字", M.fix_vocab("去暴凤眼"), "去暴风眼")
eq("2字词不误伤", M.fix_vocab("点个火"), "点个火")
eq("空串安全", M.fix_vocab(""), "")
eq("一句两词都纠", M.fix_vocab("监护入在暴风哏"), "监护人在暴风眼")

print("="*60); print("六B、fix 真机错字纠正（2026-10-15，含反例防误伤）"); print("="*60)
# —— 正例：10/15 真机实锤错字必须改对 ——
eq("亨！→哼！", M.fix("亨！那肯定"), "哼！那肯定")
eq("句首亨星河→哼星河", M.fix("亨星河自己说的"), "哼星河自己说的")
eq("亨~→哼~", M.fix("亨~"), "哼~")
eq("子啦→好啦", M.fix("子啦不闹了"), "好啦不闹了")
eq("雨林俟→雨林欸", M.fix("可以看出来是雨林俟"), "可以看出来是雨林欸")
eq("雨林埃→雨林欸", M.fix("雨林埃"), "雨林欸")
eq("林俟→雨林欸(补雨)", M.fix("是林俟"), "是雨林欸")
eq("林埃→雨林欸(补雨)", M.fix("可以看出来是林埃"), "可以看出来是雨林欸")
eq("雨林俟和林埃→雨林欸和雨林欸", M.fix("你刚说雨林俟和林埃"), "你刚说雨林欸和雨林欸")
# —— 反例：正常词绝不能被改（反向优化红线）——
eq("大亨不改", M.fix("大亨来了"), "大亨来了")
eq("亨利不改", M.fix("亨利你好"), "亨利你好")
eq("亨通不改", M.fix("亨通公司"), "亨通公司")
eq("大亨星河不改", M.fix("大亨星河"), "大亨星河")
eq("孩子不改", M.fix("孩子睡了"), "孩子睡了")
eq("桌子不改", M.fix("桌子上"), "桌子上")
eq("小兔子不改", M.fix("小兔子"), "小兔子")
eq("尘埃不改", M.fix("尘埃落定"), "尘埃落定")
eq("埃及不改", M.fix("埃及在哪"), "埃及在哪")
eq("俟机不改", M.fix("俟机而动"), "俟机而动")
eq("森林不改", M.fix("森林入口"), "森林入口")
eq("树林不改", M.fix("树林深处"), "树林深处")
eq("正确雨林不改", M.fix("在雨林等你"), "在雨林等你")
eq("正确雨林欸不改", M.fix("雨林欸"), "雨林欸")

print("="*60); print("七、ChatVoteBuffer 多行/边界"); print("="*60)
v = M.ChatVoteBuffer(window=5)
r = v.stabilize([{"text": "第一行", "confidence": 0.9},
                 {"text": "第二行", "confidence": 0.8}], 0.0)
eq("多行首帧原样", [x["text"] for x in r], ["第一行", "第二行"])
v.stabilize([{"text": "第一行", "confidence": 0.9}], 1.0)
v.stabilize([{"text": "第一行", "confidence": 0.9}], 2.0)
r = v.stabilize([{"text": "第一行l", "confidence": 0.6}], 3.0)
eq("偶发错字被拉回", r[0]["text"], "第一行")
check("长度差>1不并组",
      M.ChatVoteBuffer._same_line("你好", "你真的好呀") is False)
check("空项被跳过", v.stabilize([{"text": "  ", "confidence": 0.9}], 4.0) == [])

print("="*60); print("八、白名单过滤（临时改全局，测完还原）"); print("="*60)
saved_en, saved_wl = M.WHITELIST_ENABLED, M.WHITELIST
try:
    M.WHITELIST_ENABLED = True; M.WHITELIST = ["珂珂"]
    check("白名单本人", M._in_whitelist("珂珂") is True)
    check("前缀匹配", M._in_whitelist("珂珂呀") is True)
    check("路人拒绝", M._in_whitelist("路人甲") is False)
    filtered = M._filter_by_whitelist(["[珂珂] 在吗", "[路人] 让让", "[星河] 嗯"])
    eq("留白名单+自己,丢路人", filtered, ["[珂珂] 在吗", "[星河] 嗯"])
    M.WHITELIST_ENABLED = False
    eq("白名单关闭则全放行",
       M._filter_by_whitelist(["[路人] x"]), ["[路人] x"])
finally:
    M.WHITELIST_ENABLED, M.WHITELIST = saved_en, saved_wl

print("="*60); print("九、说话人名字归一 + 行尾已知名字兜底切分"); print("="*60)
saved_en9, saved_wl9 = M.WHITELIST_ENABLED, M.WHITELIST
try:
    M.WHITELIST_ENABLED = True; M.WHITELIST = ["珂珂", "幺幺", "阿颜"]
    eq("显式归一 幺么→幺幺", M._canonical_name("幺么"), "幺幺")
    eq("显式归一 么么→幺幺", M._canonical_name("么么"), "幺幺")
    eq("显式归一 可可→珂珂", M._canonical_name("可可"), "珂珂")
    eq("等长差1 河珂→珂珂", M._canonical_name("河珂"), "珂珂")
    eq("原样命中 阿颜", M._canonical_name("阿颜"), "阿颜")
    check("陌生人不归一", M._canonical_name("陌生人") is None)
    check("无关词不归一(路人)", M._canonical_name("路人") is None)
    eq("一兜底 最后一珂珂", M.extract("最后一珂珂"), ["[珂珂] 最后"])
    eq("一兜底 大好人啊一幺么", M.extract("大好人啊一幺么"), ["[幺幺] 大好人啊"])
    eq("标准-+名字归一 可以-幺么", M.extract("可以-幺么"), ["[幺幺] 可以"])
    eq("全角－+河珂", M.extract("爱你－河珂"), ["[珂珂] 爱你"])
    eq("反例 一起跑图绝不误切", M.extract("一起跑图"), [])
    eq("反例 正常句尾非名字不切", M.extract("我们明天再去跑图"), [])
    ms9 = M.extract("给你玩-幺么\n消失了-幺么")
    eq("两条幺幺都解析", ms9, ["[幺幺] 给你玩", "[幺幺] 消失了"])
    eq("白名单不再误杀幺幺", M._filter_by_whitelist(ms9), ms9)
finally:
    M.WHITELIST_ENABLED, M.WHITELIST = saved_en9, saved_wl9

print("="*60); print("十、发送层标签门控 extract_chat_texts（错误回复绝不外发）"); print("="*60)
eq("正常单CHAT", M.extract_chat_texts("[CHAT]你好呀[/CHAT]"), ["你好呀"])
eq("无标签思考原文不外发", M.extract_chat_texts("我想想，她可能是这个意思吧"), [])
eq("IDLE不外发", M.extract_chat_texts("[IDLE]"), [])
eq("多个CHAT保序",
   M.extract_chat_texts("[CHAT]第一句[/CHAT][ACT]牵手[/ACT][CHAT]第二句[/CHAT]"),
   ["第一句", "第二句"])
eq("CHAT混ACT只取CHAT", M.extract_chat_texts("[ACT]牵手[/ACT][CHAT]走啦[/CHAT]"), ["走啦"])
eq("空白CHAT跳过", M.extract_chat_texts("[CHAT]   [/CHAT][CHAT]嗯[/CHAT]"), ["嗯"])
eq("CHAT内首尾空白strip", M.extract_chat_texts("[CHAT]\n  在吗 \n[/CHAT]"), ["在吗"])
eq("跨行CHAT(DOTALL)", M.extract_chat_texts("[CHAT]第一行\n第二行[/CHAT]"), ["第一行\n第二行"])
eq("空串安全", M.extract_chat_texts(""), [])
check("乱码无标签不外发", M.extract_chat_texts("asdf@@@###") == [])

print("="*60); print("十一、发送前稳定关面板状态机 _stable_closed_check"); print("="*60)
check("连续2帧非开=稳定关", M._stable_closed_check([True, False, False]) is True)
check("中间抖动一次要重新计数", M._stable_closed_check([False, True, False, False]) is True)
check("末尾仅1帧非开不算", M._stable_closed_check([False, True, False]) is False)
check("一直开=未关", M._stable_closed_check([True, True, True]) is False)
check("两帧即关", M._stable_closed_check([False, False]) is True)
check("None(加载中)也算非开", M._stable_closed_check([None, False]) is True)
check("抖动后最终连续2帧关", M._stable_closed_check([True, False, True, False, False]) is True)
check("need=3时连续不足不算", M._stable_closed_check([False, False, True, False, False], need=3) is False)
check("need=3连续3帧才算", M._stable_closed_check([False, False, False], need=3) is True)
# --- YOLO优先判关（2026-09-04 传统误报导致不回消息的回归） ---
# 判关只看 panel_val（=panel_open=YOLO OR 传统）：任一通道说开就不算关
check("任一通道说开(panel_val=True)→未关", M._select_closed(True, True, True) is False)
check("panel_val=False→已关", M._select_closed(True, False, False) is True)
check("panel_val=True 即使YOLO无结论也未关", M._select_closed(True, None, True) is False)
check("panel_val=False YOLO无结论→已关", M._select_closed(True, None, False) is True)
check("无YOLO+panel_val=True→未关", M._select_closed(False, None, True) is False)
check("panel_val为False/None→已关", M._select_closed(False, None, False) is True and M._select_closed(False, None, None) is True)
check("YOLO漏检但传统说开(panel_val=True)→不得误判已关", M._select_closed(True, True, True) is False)
check("连续2帧已关才稳定", M._closed_streak_ok([False, True, True]) is True)
check("中途反复要重新计数", M._closed_streak_ok([True, False, True]) is False)
check("空序列不算关", M._closed_streak_ok([]) is False)

print("="*60); print("十二、好友树动作导航配置 friend_tree_nav_keys"); print("="*60)
eq("抱抱导航=右1上3(2026-09-10真机实跑:底行牵手右1再上3到第2行中间抱抱,旧上4会冲到顶行蜡5未解锁)", M.friend_tree_nav_keys("抱抱"), ['right', 'up', 'up', 'up'])
check("未收录动作返回None", M.friend_tree_nav_keys("不存在的动作") is None)
check("所有导航序列只含合法方向键",
      all(k in ("right", "left", "up", "down")
          for seq in M.FRIEND_TREE_NAV.values() for k in seq))

print("="*60); print("十三、发送关面板状态机 close_panel_plan"); print("="*60)
check("关闭抖动不补按",
      M.close_panel_plan([False,False,False,True,True]) ==
      ["wait","wait","wait","wait","done"])
check("进入即关快速done",
      M.close_panel_plan([True,True], initially_open=False) == ["wait","done"])
_p = M.close_panel_plan([False]*12)
check("持续开:冷静期后只补1次不狂按", _p.count("press")==1 and _p[-1]!="done")
_p2 = M.close_panel_plan([False]*8+[True,True])
check("补按后成功关闭", _p2.count("press")==1 and _p2[-1]=="done")
_p5 = M.close_panel_plan([False,False,True]+[False]*7)
check("见过关后再报开也不补按", _p5.count("press")==0)
_p3 = M.close_panel_plan([False,True,False,True,True])
check("反复抖动最终关且不补按", _p3.count("press")==0 and _p3[-1]=="done")
_p4 = M.close_panel_plan([False]*60, cooldown=2)
check("补按次数封顶max_press", _p4.count("press")<=3)

print("="*60); print("十四、看门开面板状态机 open_panel_plan"); print("="*60)
check("已开不按键", M.open_panel_plan([True,True]) == ["wait","done"])
_op = M.open_panel_plan([False,False,True,True])
check("确认关后按1次即开", _op.count("press")==1 and _op[-1]=="done")
_op2 = M.open_panel_plan([False]*10)
check("一直关最多按2次不狂按", _op2.count("press")==2 and _op2[-1]!="done")
_op3 = M.open_panel_plan([False,True,False,True,True])
check("打开抖动不误补最终开", _op3.count("press")==0 and _op3[-1]=="done")
_op4 = M.open_panel_plan([False]*9+[True,True])
check("补按后成功打开", _op4.count("press")==2 and _op4[-1]=="done")
_op5 = M.open_panel_plan([False,False,True]+[False]*8)
check("见过开后持续漏检也不补按", _op5.count("press")==1 and _op5[-1]!="done")

print("="*60); print("十五、底部提示行主裁 judge/fuse（续28 设计/续42 159帧标定/续43 接入主循环）"); print("="*60)
eq("高分->open", M.judge_panel_by_hint(0.9), "open")
eq("开阈值边界含等号->open", M.judge_panel_by_hint(0.50), "open")
eq("开帧实测主流0.82->open", M.judge_panel_by_hint(0.82), "open")
eq("关阈值边界含等号->closed", M.judge_panel_by_hint(0.20), "closed")
eq("零相关->closed", M.judge_panel_by_hint(0.0), "closed")
eq("负相关->closed", M.judge_panel_by_hint(-0.2), "closed")
eq("亮景火光关帧0.3->中间带unknown", M.judge_panel_by_hint(0.30), "unknown")
eq("开帧激活态0.474->中间带unknown", M.judge_panel_by_hint(0.474), "unknown")
eq("None(未放模板/没算分)->unknown", M.judge_panel_by_hint(None), "unknown")
check("明确开覆盖prev关", M.fuse_panel_verdict(0.9, False)==(True,"hint"))
check("明确关覆盖prev开(治旧通道咬死1.0)", M.fuse_panel_verdict(0.0, True)==(False,"hint"))
check("中间带沿用prev=True", M.fuse_panel_verdict(0.3, True)==(True,"hold"))
check("中间带沿用prev=False", M.fuse_panel_verdict(0.3, False)==(False,"hold"))
check("中间带prev=None仍None", M.fuse_panel_verdict(0.3, None)==(None,"hold"))
check("无模板分沿用prev", M.fuse_panel_verdict(None, False)==(False,"hold"))
check("自定义阈值生效", M.fuse_panel_verdict(0.5, False, open_min=0.6, closed_max=0.2)==(False,"hold"))

# ---- hint 中间带的回退融合（2026-10-06 真机事故后加）----
# 事故原形：面板**实际没开**，hint 全程 0.26~0.42 落中间带，旧写法 "YOLO OR 传统"
# 被暗场景里咬死 1.00 的传统通道带偏 -> 一直判开 -> 守门从不按C -> 面板打不开。
check("默认回退策略是 yolo（不是 legacy）", M.PANEL_FALLBACK == "yolo")
check("默认只信 YOLO 的开关为真", M.PANEL_FALLBACK_YOLO_ONLY is True)
check("【真机事故原形】YOLO在场但无检测 + 传统说开 -> 判关",
      M.fuse_panel_fallback(True, False, True) is False)
check("YOLO说开 -> 判开（即使传统说关）",
      M.fuse_panel_fallback(True, True, False) is True)
check("两路都说开 -> 判开", M.fuse_panel_fallback(True, True, True) is True)
check("两路都说关 -> 判关", M.fuse_panel_fallback(True, False, False) is False)
check("YOLO不可用 -> 只能信传统（说开判开）",
      M.fuse_panel_fallback(False, False, True) is True)
check("YOLO不可用 -> 传统说关判关", M.fuse_panel_fallback(False, True, False) is False)
check("legacy 模式可回退旧 OR 语义（YOLO无检测+传统开 -> 开）",
      M.fuse_panel_fallback(True, False, True, yolo_only=False) is True)
check("legacy 模式：YOLO说开仍然开",
      M.fuse_panel_fallback(True, True, False, yolo_only=False) is True)
check("legacy 模式：两路都关才关",
      M.fuse_panel_fallback(True, False, False, yolo_only=False) is False)
check("返回值是 bool（不是 numpy 真值/None）",
      all(isinstance(M.fuse_panel_fallback(a, b, c), bool)
          for a in (True, False) for b in (True, False) for c in (True, False)))

# 结构护栏：panel_open 必须真的走这个融合函数，别再退回内联的 OR
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sky-loop-v7.py"),
            "rb").read().decode("utf-8")
check("panel_open 里已不存在内联的 OR 融合",
      "_yolo_chat_input_detected or traditional" not in _src)
check("panel_open 调用了 fuse_panel_fallback", "fuse_panel_fallback(yolo_ok" in _src)

print("="*60); print("十六、协议兜底 ensure_chat_wrapped（续30 裸文本自动包CHAT）"); print("="*60)
ew = M.ensure_chat_wrapped
eq("裸纯文本自动包", ew("早上好呀珂珂"), "[CHAT]早上好呀珂珂[/CHAT]")
eq("去首尾空白", ew("  你好 \n"), "[CHAT]你好[/CHAT]")
eq("正常CHAT原样", ew("[CHAT]在呢[/CHAT]"), "[CHAT]在呢[/CHAT]")
eq("仅ACT不包(防把动作当聊天)", ew("[ACT]坐下[/ACT]"), "[ACT]坐下[/ACT]")
eq("ACT+CHAT原样", ew("[ACT]坐下[/ACT][CHAT]陪你[/CHAT]"), "[ACT]坐下[/ACT][CHAT]陪你[/CHAT]")
eq("IDLE原样", ew("[IDLE]"), "[IDLE]")
eq("半截开标签原样(不冒险)", ew("[CHAT]你好"), "[CHAT]你好")
eq("半截闭标签原样", ew("你好[/CHAT]"), "你好[/CHAT]")
eq("KEY原样", ew("[KEY]f 80[/KEY]"), "[KEY]f 80[/KEY]")
eq("空串原样", ew(""), "")
eq("纯空白原样", ew("   "), "   ")
check("裸文本经安全门控能取出", M.extract_chat_texts(ew("早上好呀珂珂，今天怎么这么早就上线了？"))==["早上好呀珂珂，今天怎么这么早就上线了？"])
check("ACT不被门控当聊天", M.extract_chat_texts(ew("[ACT]坐下[/ACT]"))==[])

print("="*60); print("十七、好友树 F 生效判定/补按节奏/空格前复查（续45标定·续46落地）"); print("="*60)
# 常量与离线标定依据（树稳定开 0.917~0.998 / 非树上限 0.571 / 最慢识别 6.9s）
check("门限常量 0.65/0.20/8.0/2.5/3/13.0",
      (M.FRIEND_TREE_EXPAND_MIN, M.FRIEND_TREE_EXPAND_DELTA,
       M.FRIEND_TREE_REPRESS_WAIT_FIRST, M.FRIEND_TREE_REPRESS_WAIT_NEXT,
       M.FRIEND_TREE_MAX_PRESS, M.FRIEND_TREE_WINDOW,
       M.FRIEND_TREE_STILL_OPEN_MIN) == (0.65, 0.20, 8.0, 2.5, 3, 13.0, 0.65))
# a) 分数全程低位（F 真没生效）+ 等够首补等待 -> 允许补F
check("a 全程低位等够8s->允许补F", M.friend_tree_repress_plan(1, 8.1, 0.40, 0.40, False, True) is True)
# b) 分数曾爬过绝对展开门限 -> 认定 F 已生效、永不再补
check("b 分数爬到0.70->禁止补F", M.friend_tree_repress_plan(1, 9.0, 0.70, 0.30, False, True) is False)
# c) 相对增量过门限 -> 禁止
check("c 增量0.25(0.55-0.30)->禁止补F", M.friend_tree_repress_plan(1, 9.0, 0.55, 0.30, False, True) is False)
# d) 后台通道曾为真 -> 禁止
check("d 通道曾真->禁止补F", M.friend_tree_repress_plan(1, 9.0, 0.30, 0.30, True, True) is False)
# e) 没等够 -> 不补；边界含等号
check("e 7.9s未等够->不补", M.friend_tree_repress_plan(1, 7.9, 0.40, 0.40, False, True) is False)
check("e 8.0s含等号->补", M.friend_tree_repress_plan(1, 8.0, 0.40, 0.40, False, True) is True)
# f) 首补 8.0s、后续 2.5s
check("f 首补等待8.0/后续均2.5",
      M.friend_tree_repress_wait(1) == 8.0 and M.friend_tree_repress_wait(2) == 2.5
      and M.friend_tree_repress_wait(3) == 2.5)
check("f 第2次后2.6s可补", M.friend_tree_repress_plan(2, 2.6, 0.40, 0.40, False, True) is True)
check("f 首补等待即9/14误按的修复点(2.6s不再补)",
      M.friend_tree_repress_plan(1, 2.7, 0.35, 0.35, False, True) is False)
# g) 已达按次上限 -> 不补
check("g 已按3次->不补", M.friend_tree_repress_plan(3, 99.0, 0.40, 0.40, False, True) is False)
# h) 入口图标不在 -> 不盲按
check("h 图标不在->不补", M.friend_tree_repress_plan(1, 9.0, 0.40, 0.40, False, False) is False)
# F 生效判定的边界（无模板 -1 不作证据 / 底噪缺失只看绝对门限）
check("无模板-1不作生效证据", M.friend_tree_f_effective(-1.0, None, False) is False)
check("底噪缺失只看绝对门限", M.friend_tree_f_effective(0.50, None, False) is False
      and M.friend_tree_f_effective(0.65, None, False) is True)
check("底噪0.30+增量0.22=0.52->生效", M.friend_tree_f_effective(0.52, 0.30, False) is True)
check("底噪0.30+增量0.19=0.49->未生效", M.friend_tree_f_effective(0.49, 0.30, False) is False)
# i~m) 空格发起前的"树仍在"复查
check("i 主动分0.95->仍在可发空格", M.friend_tree_still_open(0.95, False) is True)
check("j 主动分0.20且通道假->已不在", M.friend_tree_still_open(0.20, False) is False)
check("m 主动分0.20但通道真->仍判不在(不给已收起背书)", M.friend_tree_still_open(0.20, True) is False)
check("k 无模板(-1)退回通道->真", M.friend_tree_still_open(-1.0, True) is True)
check("l 无模板(-1)退回通道->假", M.friend_tree_still_open(-1.0, False) is False)
check("仍在门限边界0.65含等号", M.friend_tree_still_open(0.65, False) is True
      and M.friend_tree_still_open(0.649, False) is False)

print("="*60); print("二十一、记忆系统v3（2026-10-19：原子写/两段式/白名单把关/玩家画像）"); print("="*60)
import json, tempfile, shutil
import core.memory as CM
import core.style_learner as CS

_tmpd = tempfile.mkdtemp()
# 先把存储路径指到临时目录，绝不能碰真实 user_data
CM.MEMORY_FILE = os.path.join(_tmpd, "memory.json")
CM.SEARCH_KNOWLEDGE_FILE = os.path.join(_tmpd, "sk.json")
try:
    s, r = CM._split_profile("【稳定】\n稳定内容\n\n【最近】\n最近内容")
    eq("拆段-两标记", (s, r), ("稳定内容", "最近内容"))
    s, r = CM._split_profile("【稳定】\n只有稳定")
    eq("拆段-仅稳定", (s, r), ("只有稳定", ""))
    s, r = CM._split_profile("无标记整段")
    eq("拆段-无标记归稳定", (s, r), ("无标记整段", ""))

    legacy = {"version": 2, "profile_prompt": "旧版混合记忆", "profile_updated_at": "t",
              "raw_turns": [], "pending_turns": []}
    nm = CM._normalize_memory(legacy)
    eq("迁移-旧profile进stable", nm["profile_stable"], "旧版混合记忆")
    eq("迁移-legacy合并字段保留", nm["profile_prompt"], "旧版混合记忆")

    mem = CM._empty_memory()
    mem = CM.update_profile(mem, "【稳定】\n称呼偏好\n\n【最近】\n最近在测试")
    eq("更新-两段分别写入", (mem["profile_stable"], mem["profile_recent"]),
       ("称呼偏好", "最近在测试"))
    check("更新-legacy合并含两段",
          "称呼偏好" in mem["profile_prompt"] and "最近在测试" in mem["profile_prompt"])
    mem = CM.update_profile(mem, "无标记的新整段")
    eq("更新-无标记进stable且recent保留",
       (mem["profile_stable"], mem["profile_recent"]), ("无标记的新整段", "最近在测试"))

    mp = CM.memory_prompt(mem)
    check("渲染-两段都在", "无标记的新整段" in mp and "最近在测试" in mp)
    check("渲染-最近段带过期提示", "会过期" in mp)

    mem = CM.add_turn(mem, "玩家消息A", "回复A")
    check("原子写-生成.bak", os.path.exists(CM.MEMORY_FILE + ".bak"))
    mem = CM.add_turn(mem, "玩家消息B", "回复B")  # 再存一次，.bak 里就有 A 了
    with open(CM.MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write("{corrupt")
    rec = CM.load_memory()
    check("原子写-主文件损坏从.bak恢复",
          bool(rec["raw_turns"]) and rec["raw_turns"][-1]["player"] == "玩家消息A")

    CM.save_search_knowledge([
        {"time": "2026-10-19 12:00", "query": "今天的新鲜事", "summary": "新鲜内容"},
        {"time": "2026-09-01 12:00", "query": "过期旧事", "summary": "旧内容"},
    ])
    loaded = CM.load_search_knowledge()
    eq("TTL-过期条目被过滤", [i["query"] for i in loaded], ["今天的新鲜事"])
    eq("检索-不相关纯中文返回空", CM.search_knowledge_prompt(loaded, "呵钱非啦"), "")
    check("检索-相关仍召回", "新鲜" in CM.search_knowledge_prompt(loaded, "今天的新鲜事"))
    check("清洗-未闭合HTML残片", "<" not in CM._safe_public_text("正常 <a href=\"x 剩余"))

    eq("取名字-带括号", M._sender_name("[珂珂] 你好"), "珂珂")
    eq("取名字-无前缀", M._sender_name("没有名字的话"), "")
    check("把关-珂珂在白名单", M._in_whitelist(M._sender_name("[珂珂] 你好")) is True)
    check("把关-路人不在白名单", M._in_whitelist(M._sender_name("[路人甲] 你好")) is False)

    turns = [{"player": "我们走嘛", "companion": "好呀"}] * 10
    prof = CS.player_style_profile(turns)
    check("画像-足够样本非空", len(prof) > 10 and "语气词" in prof)
    eq("画像-样本不足返回空", CS.player_style_profile(turns[:3]), "")
finally:
    shutil.rmtree(_tmpd, ignore_errors=True)

print("="*60); print("二十二、wing_light 光翼硬门（无翼保护）"); print("="*60)
# 状态机纯函数 wing_light_step（进入防抖、退出迟滞）
s,fr,ls = M.wing_light_step(False, 100.0, False, 0, 0.0)
eq("初始-未检出保持正常", (s,fr,ls), (False,0,0.0))
s,fr,ls = M.wing_light_step(True, 100.0, False, 0, 0.0)
eq("防抖-第1帧不进入", (s,fr,ls), (False,1,0.0))
s,fr,ls = M.wing_light_step(True, 100.4, False, 1, 0.0)
eq("防抖-第2帧进入防护", (s,fr,ls), (True,2,100.4))
s,fr,ls = M.wing_light_step(True, 100.8, True, 2, 100.4)
eq("防护-持续检出刷新last_seen", (s,ls), (True,100.8))
s,fr,ls = M.wing_light_step(False, 101.5, True, 3, 100.8)
eq("迟滞-hold内未检出仍防护", (s,fr,ls), (True,0,100.8))
s,fr,ls = M.wing_light_step(False, 102.8, True, 0, 100.8)
eq("边界-等于hold仍防护", s, True)
s,fr,ls = M.wing_light_step(False, 103.0, True, 0, 100.8)
eq("迟滞-超hold解除", (s,fr,ls), (False,0,100.8))
s,fr,ls = M.wing_light_step(True, 200.0, False, 0, 0.0, frames_needed=3)
s,fr,ls = M.wing_light_step(True, 200.4, s, fr, ls, frames_needed=3)
eq("自定义-第2帧仍不进入", s, False)
s,fr,ls = M.wing_light_step(True, 200.8, s, fr, ls, frames_needed=3)
eq("自定义-第3帧进入", s, True)

# _wing_light_near 判定 + _key 拦截 F（保存/恢复全局状态，mock mcp）
_save = (M._yolo_wing_light_seen, M._yolo_wing_light_frames,
         M._yolo_wing_light_last_seen, M.WING_LIGHT_GUARD_ENABLED)
_calls = []
_real_mcp = M.mcp
def _fake_mcp(method, params, session=None):
    _calls.append((method, params.get("key")))
M.mcp = _fake_mcp
try:
    M.WING_LIGHT_GUARD_ENABLED = True
    M._yolo_wing_light_seen=False; M._yolo_wing_light_frames=0; M._yolo_wing_light_last_seen=0.0
    check("near-正常态False", M._wing_light_near(now=100.0) is False)
    _calls.clear(); M._key(None,"f",80)
    eq("key-正常态F照常发送", [c[1] for c in _calls if c[0]=="press_key"], ["f"])
    M._yolo_wing_light_seen=True; M._yolo_wing_light_last_seen=100.0
    check("near-防护态True", M._wing_light_near(now=100.4) is True)
    _calls.clear(); M._key(None,"f",80)
    eq("key-防护态F被拦截", [c for c in _calls if c[0]=="press_key"], [])
    _calls.clear(); M._key(None,"c",80)
    eq("key-防护态C仍发送", [c[1] for c in _calls if c[0]=="press_key"], ["c"])
    M._yolo_wing_light_seen=False; M._yolo_wing_light_last_seen=100.0
    check("near-迟滞兜底hold内True", M._wing_light_near(now=101.0) is True)
    check("near-过hold False", M._wing_light_near(now=103.0) is False)
    M.WING_LIGHT_GUARD_ENABLED=False; M._yolo_wing_light_seen=True
    check("near-禁用guard恒False", M._wing_light_near(now=100.0) is False)
finally:
    M.mcp = _real_mcp
    (M._yolo_wing_light_seen, M._yolo_wing_light_frames,
     M._yolo_wing_light_last_seen, M.WING_LIGHT_GUARD_ENABLED) = _save

# ===================== 第二十三节：Delta B1 拟人互动邀请事件（先反应，不按 F） =====================
print("-"*60)
print("第二十三节：Delta B1 互动邀请事件喂 AI 先反应")
print("-"*60)
import queue as _queue
import numpy as _np

# interact_sanitize_to_chat：B1 只放行纯 CHAT，绝不放行动作（防 F）
eq("sanitize-纯CHAT", M.interact_sanitize_to_chat("[CHAT]抱~[/CHAT]"), "[CHAT]抱~[/CHAT]")
eq("sanitize-CHAT后ACT只留CHAT", M.interact_sanitize_to_chat("[CHAT]抱~[/CHAT][ACT]抱抱[/ACT]"), "[CHAT]抱~[/CHAT]")
eq("sanitize-ACT后CHAT只留CHAT", M.interact_sanitize_to_chat("[ACT]点火[/ACT][CHAT]来呀[/CHAT]"), "[CHAT]来呀[/CHAT]")
check("sanitize-只有ACT不放行", M.interact_sanitize_to_chat("[ACT]抱抱[/ACT]") is None)
check("sanitize-只有KEY不放行", M.interact_sanitize_to_chat("[KEY]f[/KEY]") is None)
check("sanitize-只有IDLE不放行", M.interact_sanitize_to_chat("[IDLE]") is None)
eq("sanitize-裸文本包CHAT", M.interact_sanitize_to_chat("怎么突然抱我"), "[CHAT]怎么突然抱我[/CHAT]")
check("sanitize-空None", M.interact_sanitize_to_chat(None) is None)
check("sanitize-单字裸文本不放行", M.interact_sanitize_to_chat("嗯") is None)
eq("sanitize-CHAT带IDLE只留CHAT", M.interact_sanitize_to_chat("[CHAT]嗯～[/CHAT][IDLE]"), "[CHAT]嗯～[/CHAT]")

# interact_build_event
ev = M.interact_build_event("accept_hug", "抱抱", "珂珂", 0.92, 100.0)
eq("event-字段", (ev["action"], ev["action_cn"], ev["name"], ev["conf"], ev["time"]),
   ("accept_hug", "抱抱", "珂珂", 0.92, 100.0))
ev2 = M.interact_build_event("accept_carry", "背背", "", 0.9, 1.0)
check("event-空名字", ev2["name"] == "" and ev2["action_cn"] == "背背")

# interact_default_line
for cn in ("抱抱", "背背", "击掌", "碰拳"):
    check("default-" + cn, len(M.interact_default_line(cn)) >= 2)
check("default-未知动作兜底", len(M.interact_default_line("未知")) >= 2)
check("default-seed轮换", M.interact_default_line("抱抱", 0) != M.interact_default_line("抱抱", 1))

# interact_invite_context
_ctx = M.interact_invite_context("抱抱")
check("ctx-含动作与约束", "抱抱" in _ctx and "[CHAT]" in _ctx and "15 秒" in _ctx)

# 端到端：白名单邀请才入队（陌生人 / 好友树开 / 开关关 不入队）
class _FTv:
    def __init__(self, v): self.value = v
class _MockSnap:
    def __init__(self, ft): self.friend_tree = _FTv(ft)
_frame = _np.zeros((20, 20, 3), _np.uint8)
_save_b1 = (M.ocr_player_name_above_icon, M._accept_vision_judge,
            M.panel_open, M.INTERACT_REACT_ENABLED)
try:
    M._accept_vision_judge = lambda frame, bbox: None
    M.panel_open = lambda det: None
    M.INTERACT_REACT_ENABLED = True
    M.ocr_player_name_above_icon = lambda f, b: "珂珂"
    iq = _queue.Queue()
    M._accept_invite_event(None, None, _MockSnap(False), _frame,
                           "accept_hug", "抱抱", 0.9, (1, 1, 10, 10), 100.0, iq)
    check("queue-白名单入队1个", iq.qsize() == 1)
    _ev = iq.get_nowait()
    check("queue-入队事件动作正确", _ev["action_cn"] == "抱抱" and _ev["name"] == "珂珂")
    M.ocr_player_name_above_icon = lambda f, b: "路人甲"
    iq2 = _queue.Queue()
    M._accept_invite_event(None, None, _MockSnap(False), _frame,
                           "accept_hug", "抱抱", 0.9, (1, 1, 10, 10), 100.0, iq2)
    check("queue-陌生人不入队", iq2.qsize() == 0)
    M.ocr_player_name_above_icon = lambda f, b: "珂珂"
    iq3 = _queue.Queue()
    M._accept_invite_event(None, None, _MockSnap(True), _frame,
                           "accept_hug", "抱抱", 0.9, (1, 1, 10, 10), 100.0, iq3)
    check("queue-好友树开不入队", iq3.qsize() == 0)
    M.INTERACT_REACT_ENABLED = False
    iq4 = _queue.Queue()
    M._accept_invite_event(None, None, _MockSnap(False), _frame,
                           "accept_hug", "抱抱", 0.9, (1, 1, 10, 10), 100.0, iq4)
    check("queue-开关关闭不入队", iq4.qsize() == 0)
finally:
    (M.ocr_player_name_above_icon, M._accept_vision_judge,
     M.panel_open, M.INTERACT_REACT_ENABLED) = _save_b1

# ===================== 第二十四节：2026-10-19 代码审查修复（第六十三/六十四章） =====================
print("-"*60)
print("第二十四节：审查修复（光翼硬门看护 / 记忆跳过可见 / 台词名字 / 队列丢弃）")
print("-"*60)
import io as _io
import contextlib as _ctxlib

# ── P0：光翼硬门新鲜度看护（seen 粘住不刷新 -> 告警；默认不自动解锁）──
_save24 = (M._yolo_wing_light_seen, M._yolo_wing_light_frames,
           M._yolo_wing_light_last_seen, M._yolo_wing_light_warn_at,
           M.WING_LIGHT_GUARD_ENABLED, M.WING_LIGHT_STALE_WARN,
           M.WING_LIGHT_WARN_EVERY, M.WING_LIGHT_MAX_HOLD)
try:
    M.WING_LIGHT_GUARD_ENABLED = True
    M.WING_LIGHT_STALE_WARN = 15.0
    M.WING_LIGHT_WARN_EVERY = 10.0
    M.WING_LIGHT_MAX_HOLD = 0.0   # 默认：永不自动解除（安全优先）
    # (1) 正常防护态：last_seen 新鲜 -> 仍拦截，且不告警
    M._yolo_wing_light_seen = True; M._yolo_wing_light_last_seen = 100.0
    M._yolo_wing_light_warn_at = 0.0
    _buf = _io.StringIO()
    with _ctxlib.redirect_stdout(_buf):
        r = M._wing_light_near(now=101.0)
    check("P0-新鲜防护态仍拦截", r is True)
    check("P0-新鲜时不告警", "警告" not in _buf.getvalue(), _buf.getvalue()[:60])
    # (2) seen 粘住 1 小时未刷新 -> **仍拦截**（安全优先，绝不冒险拾取），但必须告警
    _buf = _io.StringIO()
    with _ctxlib.redirect_stdout(_buf):
        r = M._wing_light_near(now=3700.0)
    _out = _buf.getvalue()
    check("P0-陈旧仍拦截(安全优先)", r is True)
    check("P0-陈旧触发告警", "警告" in _out and "F 仍在拦截中" in _out, _out[:80])
    # (3) 告警限流：紧接着再调一次不应重复刷屏
    _buf2 = _io.StringIO()
    with _ctxlib.redirect_stdout(_buf2):
        M._wing_light_near(now=3700.5)
    check("P0-告警限流(10s内不重复)", "警告" not in _buf2.getvalue())
    # (4) 逃生开关：MAX_HOLD>0 且超时 -> 强制解除，seen 复位
    M.WING_LIGHT_MAX_HOLD = 30.0
    M._yolo_wing_light_seen = True; M._yolo_wing_light_last_seen = 100.0
    _buf3 = _io.StringIO()
    with _ctxlib.redirect_stdout(_buf3):
        r2 = M._wing_light_near(now=200.0)
    check("P0-MAX_HOLD超时强制解除", r2 is False)
    check("P0-强制解除后seen复位", M._yolo_wing_light_seen is False)
    check("P0-强制解除有日志", "强制解除" in _buf3.getvalue())
    # (5) 逃生开关默认关时绝不自动解除
    M.WING_LIGHT_MAX_HOLD = 0.0
    M._yolo_wing_light_seen = True; M._yolo_wing_light_last_seen = 100.0
    check("P0-默认MAX_HOLD=0不解除", M._wing_light_near(now=99999.0) is True)
    # (6) 硬门关闭恒 False
    M.WING_LIGHT_GUARD_ENABLED = False
    check("P0-硬门关闭恒False", M._wing_light_near(now=101.0) is False)
finally:
    (M._yolo_wing_light_seen, M._yolo_wing_light_frames,
     M._yolo_wing_light_last_seen, M._yolo_wing_light_warn_at,
     M.WING_LIGHT_GUARD_ENABLED, M.WING_LIGHT_STALE_WARN,
     M.WING_LIGHT_WARN_EVERY, M.WING_LIGHT_MAX_HOLD) = _save24

# ── P1-1：记忆跳过的可见化（低频告警，第1条必打）──
_save_cnt = M._MEM_SKIP_COUNT
try:
    M._MEM_SKIP_COUNT = 0
    outs = []
    for i in range(12):
        _b = _io.StringIO()
        with _ctxlib.redirect_stdout(_b):
            M._log_memory_skip("路人甲")
        outs.append(_b.getvalue().strip())
    check("P1-第1条必打", "跳过 1 条" in outs[0], outs[0][:60])
    check("P1-第2条不打(限流)", outs[1] == "", outs[1][:60])
    check("P1-第10条再打", "跳过 10 条" in outs[9], outs[9][:60])
    check("P1-第11条不打", outs[10] == "")
    check("P1-带发送者名", "路人甲" in outs[0])
    # 名字解析失败时给"未知"提示（重置计数，确保这次一定打印）
    M._MEM_SKIP_COUNT = 0
    _b = _io.StringIO()
    with _ctxlib.redirect_stdout(_b):
        M._log_memory_skip("")
    check("P1-空名字给未知提示", "未知" in _b.getvalue(), _b.getvalue()[:60])
finally:
    M._MEM_SKIP_COUNT = _save_cnt

# ── P1-2：互动台词不再写死"珂珂" ──
_c1 = M.interact_invite_context("抱抱")              # 向后兼容：单参数
_c2 = M.interact_invite_context("抱抱", "秋")         # 其他白名单好友
_c3 = M.interact_invite_context("抱抱", "珂珂")       # 珂珂本人
check("P1-2-兼容单参数(中性表述)", "有好友刚刚" in _c1 and "抱抱" in _c1)
check("P1-2-传入名字生效", "秋刚刚" in _c2 and "有好友" not in _c2, _c2[:60])
check("P1-2-珂珂本人", "珂珂刚刚" in _c3)
check("P1-2-动作名仍在", "抱抱" in _c2 and "[CHAT]" in _c2 and "15 秒" in _c2)

# ===================== 第二十五节：锁内慢调用防回归（第六十五章 P0） =====================
print("-"*60)
print("第二十五节：state.lock 内不得有慢调用（trim/LLM）")
print("-"*60)
import ast as _ast
import threading

_src = open(os.path.join(ROOT, "sky-loop-v7.py"), encoding="utf-8").read()
_tree = _ast.parse(_src)
_SLOW = ("trim", "llm_client.chat", "llm_client.chat_raw", "time.sleep",
         "search_web", "answer_from_search", "rewrite_repetitive_reply")
_violations = []
for _node in _ast.walk(_tree):
    if isinstance(_node, _ast.With):
        if any("state.lock" in _ast.unparse(i.context_expr) for i in _node.items):
            for _c in _ast.walk(_node):
                if isinstance(_c, _ast.Call):
                    _nm = _ast.unparse(_c.func)
                    for _s in _SLOW:
                        if _nm == _s or _nm.endswith("." + _s):
                            _violations.append((_c.lineno, _nm))
check("锁内无慢调用(trim/LLM/搜索/sleep)", not _violations, _violations)

# 回归细节：trim 必须在锁外、且 conversation 仍被正确快照写回
class _FakeLLM:
    def __init__(self): self.calls = 0
    def chat(self, messages=None, temperature=0.3, max_tokens=200, **kw):
        self.calls += 1
        return "摘要内容"
_fllm = _FakeLLM()
_conv = [{"role": "user", "content": "第%d条" % i} for i in range(40)]
_lock = threading.RLock()
# 模拟新代码的顺序：锁内快照 -> 锁外 trim -> 锁内写回
with _lock:
    _snap = list(_conv)
_trimmed = M.trim(_snap, _fllm)
with _lock:
    _conv2 = _trimmed
check("trim在超阈值时真的调了LLM", _fllm.calls == 1, _fllm.calls)
check("trim压缩后变短", len(_conv2) < len(_conv), (len(_conv2), len(_conv)))
check("trim结果含摘要", any("摘要内容" in m["content"] for m in _conv2))
check("trim首条为user角色(可继续对话)", _conv2 and _conv2[0]["role"] == "user")

# 并发验证：模拟慢 LLM 时，其他线程能否及时拿到锁（新代码必须能拿到）
class _State:
    def __init__(self): self.lock = threading.RLock(); self.flag = False
_st = _State()
_seen = []
def _watcher():
    import time as _t
    for _ in range(60):
        _t0 = _t.time()
        with _st.lock:
            _st.flag = True
            _t.sleep(0.001)
            _st.flag = False
        _seen.append(_t.time() - _t0)
        _t.sleep(0.005)
_w = threading.Thread(target=_watcher, daemon=True); _w.start()

class _SlowLLM:
    def chat(self, messages=None, **kw):
        import time as _t
        _t.sleep(1.0)          # 模拟 1 秒的 LLM 调用
        return "慢摘要"
# 新代码：trim 在锁外
with _st.lock:
    _tmp = [{"role": "user", "content": "x"} for _ in range(40)]
_tmp = M.trim(_tmp, _SlowLLM())
with _st.lock:
    _st.flag = True
import time as _t2
_t2.sleep(1.2)
_w.join(timeout=3)
_maxwait = max(_seen) if _seen else 99
check("慢LLM期间其他线程仍能拿锁(无全局冻结)", _maxwait < 0.2, "最大等待 %.3fs" % _maxwait)
check("慢LLM期间watcher确实跑了很多轮", len(_seen) >= 20, len(_seen))

# ===================== 第二十六节：邀请事件级去重（持续图标只喂一次/消失宽限/动作独立） =====================
print("-"*60)
print("第二十六节：事件级去重 + 消失宽限 + 待处理邀请状态")
print("-"*60)
_save26 = (M._accept_seen, M._accept_event_time, M._accept_present,
           M._accept_first_seen, M._accept_event_fed, M._accept_miss,
           M._yolo_accept, M.INTERACT_REACT_ENABLED, M._accept_vision_judge,
           M.ocr_player_name_above_icon)
def _reset_accept():
    for k in M._accept_seen: M._accept_seen[k] = 0
    for k in M._accept_event_time: M._accept_event_time[k] = None
    for k in M._accept_present: M._accept_present[k] = False
    for k in M._accept_first_seen: M._accept_first_seen[k] = None
    for k in M._accept_event_fed: M._accept_event_fed[k] = False
    for k in M._accept_miss: M._accept_miss[k] = 0
try:
    _reset_accept()
    M.INTERACT_REACT_ENABLED = True
    M._accept_vision_judge = lambda f, b: None
    M.ocr_player_name_above_icon = lambda f, b: "珂珂"
    iq = _queue.Queue()
    st = M.SharedState()
    def set_yolo(hit_hug):
        M._yolo_accept = {}
        if hit_hug is not None:
            M._yolo_accept["accept_hug"] = hit_hug
    # 连续 20 拍抱抱图标在屏（模拟持续存在）
    for i in range(20):
        set_yolo((0.94, (1, 1, 10, 10)))
        M._handle_accept_events(None, st, _frame, 100.0 + i * 0.4, iq)
    check("26-持续图标只入队1次", iq.qsize() == 1, iq.qsize())
    check("26-pending记录抱抱", "accept_hug" in st.pending_invites)
    # 单帧漏检（小于宽限3）→ 不重置、不重复
    set_yolo(None)
    M._handle_accept_events(None, st, _frame, 110.0, iq)
    check("26-单帧漏检仍pending", "accept_hug" in st.pending_invites and iq.qsize() == 1)
    # 下一拍恢复在屏，miss 清零，不重复
    set_yolo((0.94, (1, 1, 10, 10)))
    M._handle_accept_events(None, st, _frame, 110.4, iq)
    check("26-漏检恢复不重复", iq.qsize() == 1)
    # 连续 3 拍漏检（111.0/111.4/111.8）→ 真消失，pending 清除
    for tt in (111.0, 111.4, 111.8):
        set_yolo(None)
        M._handle_accept_events(None, st, _frame, tt, iq)
    check("26-连续漏检真消失清pending",
          "accept_hug" not in st.pending_invites and iq.qsize() == 1)
    # 玩家重新发起 → 新事件，应再入队 1 次
    for i in range(2):
        set_yolo((0.94, (1, 1, 10, 10)))
        M._handle_accept_events(None, st, _frame, 120.0 + i * 0.4, iq)
    check("26-重新发起再入队", iq.qsize() == 2, iq.qsize())
    # 动作独立：抱抱 + 背背同时在屏，各喂一次
    _reset_accept()
    iq2 = _queue.Queue()
    st2 = M.SharedState()
    M._yolo_accept = {"accept_hug": (0.94, (1, 1, 10, 10)),
                      "accept_carry": (0.90, (2, 2, 12, 12))}
    for i in range(3):
        M._handle_accept_events(None, st2, _frame, 200.0 + i * 0.4, iq2)
    check("26-两动作各入队1次", iq2.qsize() == 2, iq2.qsize())
    check("26-两动作都pending",
          "accept_hug" in st2.pending_invites and "accept_carry" in st2.pending_invites)
finally:
    (M._accept_seen, M._accept_event_time, M._accept_present,
     M._accept_first_seen, M._accept_event_fed, M._accept_miss,
     M._yolo_accept, M.INTERACT_REACT_ENABLED, M._accept_vision_judge,
     M.ocr_player_name_above_icon) = _save26

print("\n===== 第二十七节：记忆 v4（事件日记层 + RECALL协议 + 对话持久化）=====")
import tempfile as _tempfile, shutil as _shutil, time as _time
import core.memory as _CM

_tmp27 = _tempfile.mkdtemp()
_save_cm_diary = _CM.DIARY_FILE
_save_conv_file = M.CONVERSATION_FILE
_save_recalls = M.RECALL_COOLDOWN
_CM.DIARY_FILE = os.path.join(_tmp27, "diary.json")
M.CONVERSATION_FILE = os.path.join(_tmp27, "conversation.json")
try:
    # ── 1. diary 存储 ──
    n = CM.add_diary_entry("首次相遇于遇境，珂珂主动搭话|首次|相遇|遇境")
    check("27-diary单行追加", n == 1, n)
    n = CM.add_diary_entry("聊了宝马实习的打算|宝马|实习\n无|无\n看了犬夜叉聊剧情|犬夜叉")
    check("27-diary多行追加且跳过无|无", n == 2, n)
    n = CM.add_diary_entry("聊了宝马实习的打算|宝马|实习")
    check("27-diary重复事件不入库", n == 0, n)
    d = CM.load_diary()
    check("27-diary入库3条带tags", len(d) == 3 and d[0]["tags"] == ["首次", "相遇", "遇境"], d)

    # ── 2. 检索（同义词/标签/噪声过滤/空手）──
    h = CM.diary_recall("我们第一次见面的时候是在哪里呀")
    check("27-检索同义词(第一次见面→首次)", any("首次相遇" in i["event"] for _, i in h), h)
    h = CM.diary_recall("还记得聊宝马实习那天说了什么吗")
    check("27-检索标签命中宝马", bool(h) and "宝马" in h[0][1]["event"], h)
    check("27-检索噪声过滤(纯字面2~3分不注入)",
          CM.diary_recall("呵呵呵钱钱钱非非非") == [])
    check("27-检索弱相关不注入(min_score=4)",
          CM.diary_recall("好想睡觉觉呀") == [])
    r = CM.diary_recent(2)
    check("27-diary_recent最近2条", len(r) == 2 and "犬夜叉" in r[-1]["event"], r)

    # ── 3. update_profile 三段解析（日记段顺带入库）──
    mem = CM._empty_memory()
    CM.MEMORY_FILE = os.path.join(_tmp27, "memory.json")
    before = len(CM.load_diary())
    mem = CM.update_profile(mem, "【稳定】\n珂珂在宝马定向班\n\n【最近】\n最近聊了实习\n\n【日记】\n决定了先去宝马实习拿证|宝马|决定")
    check("27-三段解析各归其位",
          "宝马定向班" in mem["profile_stable"] and "实习" in mem["profile_recent"])
    check("27-整合日记自动入库", len(CM.load_diary()) == before + 1)
    mem = CM.update_profile(mem, "【稳定】\n珂珂喜欢短句\n\n【最近】\n最近在挂机")
    check("27-两段输入不误写日记", len(CM.load_diary()) == before + 1)

    # ── 4. RECALL 协议（mock LLM/state）──
    class _FakeLLM:
        def __init__(self, reply): self.reply = reply; self.calls = 0
        def chat_raw(self, messages, temperature=0.7, max_tokens=300, tools=None):
            self.calls += 1
            return {"content": self.reply}
    class _FakeState:
        recall_last_used = 0.0
    _llm = _FakeLLM("[RECALL]犬夜叉[/RECALL]")
    _st = _FakeState()
    _wm = [{"role": "system", "content": "x"}]
    r, used = M._apply_recall_protocol(_llm.reply, _wm, _st, _llm, "还记得犬夜叉吗")
    check("27-RECALL命中重答一次", used and _llm.calls == 1, (used, _llm.calls))
    check("27-RECALL结果剥离标签", "RECALL" not in r, r)
    check("27-RECALL检索结果进work_messages", "犬夜叉" in _wm[2]["content"])
    _st.recall_last_used = _time.time()
    _llm2 = _FakeLLM("[RECALL]犬夜叉[/RECALL]")
    r2, used2 = M._apply_recall_protocol(_llm2.reply, [], _st, _llm2, "还记得吗")
    check("27-RECALL冷却期内不调LLM", not used2 and _llm2.calls == 0)
    _llm3 = _FakeLLM("[RECALL]再来一次[/RECALL]")
    r3, _ = M._apply_recall_protocol("[RECALL]外星人[/RECALL]", [], _FakeState(), _llm3, "")
    check("27-RECALL重答再带标签也剥离", "RECALL" not in r3 and _llm3.calls == 1, r3)
    r4, used4 = M._apply_recall_protocol("今天天气真好", [], _FakeState(), _FakeLLM("x"), "")
    check("27-普通回复不受影响", not used4 and r4 == "今天天气真好", r4)
    check("27-interact路径也剥离RECALL",
          "RECALL" not in (M.interact_sanitize_to_chat("[RECALL]犬夜叉[/RECALL]来抱抱吧") or ""))

    # ── 5. 对话持久化 ──
    check("27-无文件空列表启动", M._load_conversation() == [])
    conv = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "嗨"}]
    M._save_conversation(conv)
    check("27-对话保存加载往返", M._load_conversation() == conv)
    big = [{"role": "user", "content": "m%d" % i} for i in range(200)]
    M._save_conversation(big)
    back = M._load_conversation()
    check("27-对话超MAX_MSGS截断保最新",
          len(back) == M.MAX_MSGS and back[-1]["content"] == "m199", len(back))
    good = [{"role": "user", "content": "重要对话"}]
    M._save_conversation(good); M._save_conversation(good)
    open(M.CONVERSATION_FILE, "w").write("{corrupt!!!")
    check("27-对话主文件损坏从.bak恢复", M._load_conversation() == good)

    # ── 6. AST 接线断言（防将来改丢）──
    _src27 = open(os.path.join(ROOT, "sky-loop-v7.py"), encoding="utf-8").read()
    check("27-ai_loop接了RECALL协议", "_apply_recall_protocol(\n                reply, work_messages" in _src27
          or "_apply_recall_protocol(" in _src27)
    check("27-system注入日记四层", "## 我们的记忆日记" in _src27 and "## 相关往日记忆" in _src27)
    check("27-两个对话保存点在", _src27.count("_save_conversation(") >= 3, _src27.count("_save_conversation("))
    check("27-启动加载对话在State", "_load_conversation()" in _src27)
    check("27-整合prompt要求日记段", "【日记】" in _src27 or "【日记】" in open(
        os.path.join(ROOT, "core", "memory.py"), encoding="utf-8").read())
finally:
    _CM.DIARY_FILE = _save_cm_diary
    M.CONVERSATION_FILE = _save_conv_file
    M.RECALL_COOLDOWN = _save_recalls
    _shutil.rmtree(_tmp27, ignore_errors=True)

print("\n===== 第二十八节：光翼 UI 提示 F 安全窗（第七十章 P0 修复）=====")

# f_prompt_press_allowed 是纯函数：直接传参，不依赖全局，离线可单测。
_now = 1000.0

# 1) 最近刚见过光翼 -> 拒绝（这是 P0 的核心：open_tree_icon 与 wing_light 竞争分类时，
#    硬门那一拍未必亮，靠时间窗兜住）
_ok, _why = M.f_prompt_press_allowed(now=_now, last_seen=_now - 5.0,
                                     guard_online=True, window=30.0,
                                     allow_without_guard=False, guard_enabled=True)
check("28-见过光翼5s内禁按F", (not _ok) and "光翼" in _why, _why)

# 2) 超过安全窗 -> 放行
_ok, _why = M.f_prompt_press_allowed(now=_now, last_seen=_now - 31.0,
                                     guard_online=True, window=30.0,
                                     allow_without_guard=False, guard_enabled=True)
check("28-超安全窗放行", _ok and _why == "", _why)

# 3) 边界：正好等于窗口 -> 仍在窗内（宁可多拦）
_ok, _ = M.f_prompt_press_allowed(now=_now, last_seen=_now - 30.0,
                                  guard_online=True, window=30.0,
                                  allow_without_guard=False, guard_enabled=True)
check("28-边界等于窗口仍拒绝", not _ok)

# 4) 硬门不在线（YOLO 挂了 / 模型无 wing_light 类）-> fail-safe 拒绝
_ok, _why = M.f_prompt_press_allowed(now=_now, last_seen=0.0,
                                     guard_online=False, window=30.0,
                                     allow_without_guard=False, guard_enabled=True)
check("28-硬门不在线拒绝按F", (not _ok) and "未生效" in _why, _why)

# 5) 显式放行开关（用户确认环境安全）
_ok, _ = M.f_prompt_press_allowed(now=_now, last_seen=0.0,
                                  guard_online=False, window=30.0,
                                  allow_without_guard=True, guard_enabled=True)
check("28-显式放行开关生效", _ok)

# 6) 用户手动关硬门 -> 尊重其选择，不再拦
_ok, _why = M.f_prompt_press_allowed(now=_now, last_seen=_now - 1.0,
                                     guard_online=True, window=30.0,
                                     allow_without_guard=False, guard_enabled=False)
check("28-手动关硬门不拦", _ok and "手动关闭" in _why, _why)

# 7) 从没见过光翼且硬门在线 -> 放行
_ok, _why = M.f_prompt_press_allowed(now=_now, last_seen=0.0,
                                     guard_online=True, window=30.0,
                                     allow_without_guard=False, guard_enabled=True)
check("28-从未检出光翼放行", _ok and _why == "", _why)

# 8) 接线断言：好友树按 F 与 watch 牵手按 F 都必须过这道闸（防将来改丢）
_src28 = open(os.path.join(ROOT, "sky-loop-v7.py"), encoding="utf-8").read()
check("28-好友树按F过安全闸", "def _press_f():" in _src28
      and "f_prompt_press_allowed()" in _src28)
check("28-牵手按F过安全闸", "拒绝按 F 牵手" in _src28)
check("28-启动日志有硬门状态", "光翼硬门=%s" in _src28)
check("28-模型缺光翼类会告警", "光翼硬门实际未生效" in _src28)
check("28-光翼类名可配置", "SKY_WING_LIGHT_CLASS_NAME" in _src28)
check("28-watch节拍可观测", "[Watch] 节拍实测" in _src28)

# 9) MCP 侧前台保护：SKY_SKIP_FOCUS=1 不再旁路"校验"（只旁路"抢焦点"）
_mcp_src = open(os.path.join(ROOT, "sky-mcp-server.py"), encoding="utf-8").read()
check("28-MCP有严格前台校验", "_focus_strict" in _mcp_src and "SKY_FOCUS_STRICT" in _mcp_src)
check("28-press_key拒绝非前台", "refusing to press key" in _mcp_src)
check("28-send_chat拒绝非前台", "Ctrl+V would paste" in _mcp_src)

print("\n===== 第二十九节：记忆 v5（分层记忆：周记周一切分/月记/年记）=====")
import tempfile as _tempfile2, shutil as _shutil2
from datetime import date as _date

_tmp29 = _tempfile2.mkdtemp()
_save_files = {
    "diary": _CM.DIARY_FILE, "weekly": _CM.WEEKLY_FILE,
    "monthly": _CM.MONTHLY_FILE, "yearly": _CM.YEARLY_FILE,
}
_CM.DIARY_FILE = os.path.join(_tmp29, "diary.json")
_CM.WEEKLY_FILE = os.path.join(_tmp29, "weekly.json")
_CM.MONTHLY_FILE = os.path.join(_tmp29, "monthly.json")
_CM.YEARLY_FILE = os.path.join(_tmp29, "yearly.json")
try:
    # ── 1. 周期切分（周一是 ISO 周起点）──
    check("29-周一10-05属W41", _CM.week_key_for(_date(2026, 10, 5)) == "2026-W41")
    check("29-周日10-11仍属W41", _CM.week_key_for(_date(2026, 10, 11)) == "2026-W41")
    check("29-10-12跨入W42", _CM.week_key_for(_date(2026, 10, 12)) == "2026-W42")
    check("29-10-19属W43", _CM.week_key_for(_date(2026, 10, 19)) == "2026-W43")
    wr = _CM.week_range("2026-W41")
    check("29-week_range反推周一/周日",
          wr == (_date(2026, 10, 5), _date(2026, 10, 11)), wr)
    check("29-ISO跨年(2027-01-01属2026-W53)",
          _CM.week_key_for(_date(2027, 1, 1)) == "2026-W53")
    wr2 = _CM.week_range("2026-W53")
    check("29-跨年周range跨日历年",
          wr2 == (_date(2026, 12, 28), _date(2027, 1, 3)), wr2)
    check("29-月键/年键",
          _CM.month_key_for(_date(2026, 10, 24)) == "2026-10"
          and _CM.year_key_for(_date(2026, 10, 24)) == "2026")

    # ── 2. 分层存储（往返/排序/去重/截断）──
    _CM.save_weekly([
        {"period": "2026-W42", "start": "2026-10-12", "end": "2026-10-18",
         "text": "这周和珂珂在雨林挂机，还聊了实习。", "tags": ["雨林", "实习"]},
        {"period": "2026-W41", "start": "2026-10-05", "end": "2026-10-11",
         "text": "首次相遇的一周。", "tags": ["首次", "相遇"]},
    ])
    wk = _CM.load_weekly()
    check("29-周记按period排序", [w["period"] for w in wk] == ["2026-W41", "2026-W42"], wk)
    _CM.save_weekly(_CM.load_weekly() + [
        {"period": "2026-W41", "text": "重复周期不入库", "tags": []}])
    check("29-同period去重", len(_CM.load_weekly()) == 2)
    check("29-weekly_recent取最新", _CM.weekly_recent(1)[0]["period"] == "2026-W42")

    # ── 3. due 计算（只压缩已结束周期）──
    events = [
        {"time": "2026-10-06 20:00", "event": "W41事件甲", "tags": ["甲"]},
        {"time": "2026-10-10 20:00", "event": "W41事件乙", "tags": ["乙"]},
        {"time": "2026-10-13 20:00", "event": "W42事件丙", "tags": ["丙"]},
        {"time": "2026-10-22 20:00", "event": "W43事件丁", "tags": ["丁"]},
    ]
    # 清掉上一步的周记
    _CM.save_weekly([])
    today = _date(2026, 10, 24)
    due = _CM.due_periods(events=events, today=today)
    check("29-周记due含W41/W42", due["weekly"] == ["2026-W41", "2026-W42"], due)
    check("29-本周W43不due", "2026-W43" not in due["weekly"])
    check("29-当月不due月记", due["monthly"] == [])
    check("29-当年不due年记", due["yearly"] == [])

    # ── 4. 压缩编排（fake client，幂等）──
    class _FakeSummaryClient:
        def __init__(self): self.calls = 0
        def chat(self, prompt, temperature=0.2, max_tokens=1000):
            self.calls += 1
            return "【总结】\n这一周和珂珂一起在雨林挂机，聊了实习的事，还约定了下次抱抱。\n【标签】\n雨林|实习|抱抱|约定"
    fc = _FakeSummaryClient()
    added = _CM.summarize_due_periods(fc, companion_name="星河",
                                      events=events, today=today)
    check("29-压缩生成2篇周记", added["weekly"] == 2, added)
    check("29-周记已落库", len(_CM.load_weekly()) == 2)
    wk41 = [w for w in _CM.load_weekly() if w["period"] == "2026-W41"][0]
    check("29-周记正文/标签解析",
          "雨林挂机" in wk41["text"] and wk41["tags"] == ["雨林", "实习", "抱抱", "约定"], wk41)
    added2 = _CM.summarize_due_periods(fc, companion_name="星河",
                                       events=events, today=today)
    check("29-重复执行幂等无新增", added2["weekly"] == 0, added2)

    # ── 5. 月记（跨月后直接从事件+周记压缩）──
    _CM.save_monthly([])
    nov = _date(2026, 11, 2)
    due_nov = _CM.due_periods(events=events, today=nov)
    check("29-11月初10月月记due", due_nov["monthly"] == ["2026-10"], due_nov)
    fc2 = _FakeSummaryClient()
    added3 = _CM.summarize_due_periods(fc2, companion_name="星河",
                                       events=events, today=nov)
    check("29-月记生成", added3["monthly"] == 1 and len(_CM.load_monthly()) == 1, added3)
    check("29-monthly_recent可取", _CM.monthly_recent(1)[0]["period"] == "2026-10")

    # ── 6. 年记（依赖月记，跨年才生成）──
    _CM.save_yearly([])
    jan = _date(2027, 1, 5)
    # 2026-12 月记也补齐才会有年记
    _CM.save_monthly(_CM.load_monthly() + [
        {"period": "2026-12", "start": "2026-12-01", "end": "2026-12",
         "text": "12月的事。", "tags": ["12月"]}])
    due_jan = _CM.due_periods(events=events, today=jan)
    check("29-2027年初2026年记due", due_jan["yearly"] == ["2026"], due_jan)
    added4 = _CM.summarize_due_periods(_FakeSummaryClient(), companion_name="星河",
                                       events=events, today=jan)
    check("29-年记生成", added4["yearly"] == 1 and len(_CM.load_yearly()) == 1, added4)

    # ── 7. 输出解析（标准/纯文本兼容）──
    t, tags = _CM.parse_summary_output(
        "【总结】\n一段总结正文。\n【标签】\n甲|乙|丙", 600)
    check("29-标准解析", t == "一段总结正文。" and tags == ["甲", "乙", "丙"], (t, tags))
    t2, _ = _CM.parse_summary_output("只有一段纯文本的总结。", 600)
    check("29-纯文本兼容", t2 == "只有一段纯文本的总结。", t2)

    # ── 8. 分层检索（标签命中/噪声过滤）──
    hits = _CM.summary_recall("聊聊实习的事", levels=("weekly",))
    check("29-分层检索标签命中", bool(hits) and hits[0][1] == "weekly", hits)
    check("29-分层检索噪声过滤",
          _CM.summary_recall("啊啊啊啊呀呀呀", levels=("weekly", "monthly", "yearly")) == [])

    # ── 8.5 RECALL 同时检索事件日记与分层总结 ──
    _CM.save_diary([
        {"time": "2026-10-06 20:00", "event": "和珂珂聊了犬夜叉剧情", "tags": ["犬夜叉"]},
    ])
    _CM.save_weekly([
        {"period": "2026-W41", "start": "2026-10-05", "end": "2026-10-11",
         "text": "这周和珂珂一起看了犬夜叉，还聊了很多。", "tags": ["犬夜叉", "剧情"]},
    ])

    class _FakeLLM8:
        def chat_raw(self, messages, temperature=0.7, max_tokens=300, tools=None):
            return {"content": "我想起来啦，我们一起看了犬夜叉"}

    class _FakeState8:
        recall_last_used = 0.0

    _wm8 = [{"role": "system", "content": "x"}]
    r8, used8 = M._apply_recall_protocol(
        "[RECALL]犬夜叉[/RECALL]", _wm8, _FakeState8(), _FakeLLM8(), "还记得犬夜叉吗")
    _feed8 = _wm8[2]["content"]
    check("29-RECALL同时搜事件与分层",
          used8 and "【事件日记】" in _feed8 and "【周记/月记/年记】" in _feed8, _feed8[:120])
    check("29-RECALL结果剥离标签", "RECALL" not in r8, r8)
    _wm9 = []
    r9, used9 = M._apply_recall_protocol(
        "[RECALL]外星人[/RECALL]", _wm9, _FakeState8(), _FakeLLM8(), "")
    check("29-RECALL空手提示诚实",
          "没有找到相关记录" in _wm9[1]["content"], _wm9[1]["content"][:80])

    # ── 9. AST 接线断言（防将来改丢）──
    _src29 = open(os.path.join(ROOT, "sky-loop-v7.py"), encoding="utf-8").read()
    check("29-启动接了压缩", "summarize_due_periods(" in _src29)
    check("29-system注入周记", "## 周记" in _src29)
    check("29-周/月/年触发词表在",
          "WEEKLY_RECALL_WORDS" in _src29 and "YEARLY_RECALL_WORDS" in _src29)
finally:
    _CM.DIARY_FILE = _save_files["diary"]
    _CM.WEEKLY_FILE = _save_files["weekly"]
    _CM.MONTHLY_FILE = _save_files["monthly"]
    _CM.YEARLY_FILE = _save_files["yearly"]
    _shutil2.rmtree(_tmp29, ignore_errors=True)

print("="*60)
print(f"结果: PASS={PASS} FAIL={FAIL}")
print("="*60)
sys.exit(1 if FAIL else 0)
