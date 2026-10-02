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

print("="*60)
print(f"结果: PASS={PASS} FAIL={FAIL}")
print("="*60)
sys.exit(1 if FAIL else 0)
