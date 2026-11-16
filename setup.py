# -*- coding: utf-8 -*-
"""星河 · 一键配置向导（双击 setup.bat 或 `py setup.py`）。

把原来分散的设置收敛到一个数字菜单，不会改代码、不用手编 json：

  [1] API 配置   写入 key.txt / vision_key.txt（这两个文件已被打包脚本排除，不会外传）
  [2] 键位设置   点火/收火、鞠躬、回遇境、坐躺（core/keybind_settings.py）
  [3] 弹琴设置   乐器槽位 + 乐器类型（core/song_settings.py）
  [4] 光翼设置   有翼标记 + 自动拾取（core/wing_settings.py，安全默认）
  [5] 白名单设置 回复哪些玩家（core/whitelist_settings.py）
  [6] 性格设置   问答生成 user_data/persona.txt（不覆盖项目根默认人设）

所有子模块都带安全默认值，直接回车 = 保持原样。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core import keybind_settings as keyb  # noqa: E402
from core import song_settings as song  # noqa: E402
from core import wing_settings as wing  # noqa: E402
from core import whitelist_settings as wl  # noqa: E402

KEY_FILE = os.path.join(ROOT, "key.txt")
VISION_KEY_FILE = os.path.join(ROOT, "vision_key.txt")
USER_PERSONA = os.path.join(ROOT, "user_data", "persona.txt")


# ---------------- 通用输入 ----------------

def _ask_choice(prompt, valid):
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            return None
        if raw == "":
            print("  （直接回车 = 不改/返回）")
            return None
        if raw in valid:
            return raw
        print("  输入无效，可选项：%s" % " / ".join(sorted(valid)))


def _ask_line(prompt, default=""):
    """自由文本；空输入返回 default。"""
    try:
        raw = input(prompt).strip()
    except EOFError:
        return default
    return raw if raw else default


def _ask_yn(prompt, default_no=True):
    hint = "[y/N]" if default_no else "[Y/n]"
    while True:
        try:
            raw = input("%s %s: " % (prompt, hint)).strip().lower()
        except EOFError:
            return not default_no
        if not raw:
            return not default_no
        if raw in ("y", "yes", "是", "1"):
            return True
        if raw in ("n", "no", "否", "0"):
            return False
        print("  输入 y 或 n（回车 = %s）" % ("否" if default_no else "是"))


def _mask(text):
    if not text:
        return "未设置"
    if len(text) <= 8:
        return "*" * len(text)
    return text[:3] + "*" * (len(text) - 7) + text[-4:]


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.strip())


# ---------------- [1] API ----------------

def menu_api():
    print("\n" + "=" * 56)
    print("  API 配置")
    print("=" * 56)
    print("  当前聊天 key：%s" % _mask(_read_text(KEY_FILE)))
    print("  当前视觉 key：%s" % _mask(_read_text(VISION_KEY_FILE)))
    print("  提示：输入会写入本地文件，不会显示完整内容；直接回车=不改。")
    main_key = _ask_line("  请输入聊天(LLM) API Key：", _read_text(KEY_FILE))
    if main_key:
        _write_text(KEY_FILE, main_key)
        print("  已保存聊天 key：%s" % _mask(main_key))
    vision_key = _ask_line("  请输入视觉(VLM) API Key（没有可直接回车跳过）：",
                          _read_text(VISION_KEY_FILE))
    if vision_key:
        _write_text(VISION_KEY_FILE, vision_key)
        print("  已保存视觉 key：%s" % _mask(vision_key))


# ---------------- [2] 键位 ----------------

def menu_keybind():
    print("\n" + "=" * 56)
    print("  键位设置（1~8 快捷栏）")
    print("=" * 56)
    fields = [
        ("fire_slot", "点火/收火"),
        ("bow_slot", "鞠躬"),
        ("gohome_slot", "回遇境"),
        ("pose_slot", "姿势(蹲/坐/躺)"),
    ]
    cur = keyb.load_settings()
    print("  " + keyb.describe(cur))
    updates = {}
    for key, label in fields:
        v = _ask_choice("  %s 当前=%d，输入新槽位 1~8（回车=不改）："
                        % (label, cur[key]), {str(i) for i in range(1, 9)})
        if v is not None:
            updates[key] = int(v)
    if updates:
        cur = keyb.save_settings(updates)
        print("  已保存：%s" % keyb.describe(cur))


# ---------------- [3] 弹琴 ----------------

def menu_song():
    print("\n" + "=" * 56)
    print("  弹琴设置")
    print("=" * 56)
    cur = song.load_settings()
    print("  当前：%s" % song.describe(cur))
    v = _ask_choice("  乐器槽位 1~8（0=清空，回车=不改）：",
                    {str(i) for i in range(0, 9)})
    if v is not None:
        cur = song.save_settings({"instrument_slot": int(v)})
        print("  已保存槽位")
    print("  乐器类型：")
    order = ("piano", "violin", "flute", "harmonica")
    for i, k in enumerate(order, 1):
        print("    %d) %s" % (i, song.KIND_LABELS[k]))
    k = _ask_choice("  选择 1~4（回车=不改）：", {str(i) for i in range(1, 5)})
    if k is not None:
        cur = song.save_settings({"instrument_kind": order[int(k) - 1]})
        print("  已保存类型：%s" % song.KIND_LABELS[order[int(k) - 1]])
    print("  最终：%s" % song.describe(song.load_settings()))


# ---------------- [4] 光翼 ----------------

def menu_wing():
    print("\n" + "=" * 56)
    print("  光翼设置（无翼捡光翼不可逆，默认全关）")
    print("=" * 56)
    cur = wing.load_settings()
    print("  当前：%s" % wing.describe(cur))
    winged = _ask_yn("  这个号是有翼号吗？", default_no=True)
    pickup = False
    if winged:
        pickup = _ask_yn("  开启靠近光翼自动拾取？", default_no=True)
    else:
        print("  无翼号 → 自动拾取强制关闭。")
    new = wing.save_settings({"account_winged": winged, "auto_pickup_wing": pickup})
    enabled, reason = wing.resolve_pickup(os.environ.get("SKY_AUTO_PICKUP_WING", ""), new)
    print("  已保存：%s" % wing.describe(new))
    print("  生效判定：%s（%s）" % ("开启" if enabled else "关闭", reason))


# ---------------- [5] 白名单 ----------------

def menu_whitelist():
    print("\n" + "=" * 56)
    print("  白名单设置")
    print("=" * 56)
    cur = wl.load_settings()
    print("  当前：%s" % wl.describe(cur))
    enabled = _ask_yn("  开启白名单（关闭后所有人可触发）？", default_no=False)
    print("  多个名字用英文逗号分隔，例如：珂珂,幺幺,阿颜")
    names = _ask_line("  请输入名单（回车=不改）：", ",".join(wl.names_of(cur)))
    updates = {"whitelist_enabled": enabled}
    if names:
        parsed = wl.parse_names(names)
        if parsed:
            updates["whitelist"] = parsed
    new = wl.save_settings(updates)
    print("  已保存：%s" % wl.describe(new))


# ---------------- [6] 性格（persona） ----------------

PERSONA_TEMPLATE = """你是{ta_name}，{user_name}的{relation}。你们在光遇里一起玩。TA游戏名叫{game_name}。

聊天消息格式是 [名字] 内容。[{game_name}] 就是TA本人，其他都是路人或TA朋友。

## 你是什么样的人

{personality}

## 你说话的感觉

{style}

## 路人和TA朋友

不是[{game_name}]的消息看情况回。路人大部分不理，回[IDLE]就行。
亲昵的称呼只对{user_name}用。

## 回复格式（协议，必须遵守）

一次最多一条CHAT，可以组合动作：
  [CHAT]说的话[/CHAT]
  [ACT]点火/收火/鞠躬/蹲下/坐下/躺下/回遇境/牵手/回家牵手/弹琴:歌名/停止弹琴[/ACT]
  [KEY]键名 毫秒[/KEY]
  [IDLE]

按键（光遇 PC 版）：
  w/a/s/d 移动；space 跳跃/飞行；f 交互/点火；q 呼喊；tab 切换飞行；e 打开快捷栏；
  x 物品能力；esc 普通界面会打开设置（不要在普通界面按）；c 程序用于关面板（不要自己按）；
  m 地图、enter 聊天框都由程序处理，不要自己按。
  快捷栏按 E 打开，方向键移动光标、space 选择、esc 退出；盲操作容易选错，做完动作记得退出。

点火后火会一直举着，直到[ACT]收火[/ACT]才放下；举火会晃到眼睛看不清聊天面板。
蹲/坐/躺是同一个快捷栏键按不同次数循环；回遇境按对应快捷栏后程序会自动确认。
牵手图标出现时程序会自动按 F，你不用自己输出[ACT]牵手；TA没伸手时可以先用话引导。
弹琴：TA说"弹XX"时输出[ACT]弹琴:歌名[/ACT]，程序找曲谱、确认乐器界面后再演奏；
TA说"别弹了/停一下"时输出[ACT]停止弹琴[/ACT]。界面打不开就如实说，不要硬说弹了。

没有新消息就[IDLE]安静等待；{user_name}说话了不准[IDLE]。
程序持续运行，不能主动说结束；停止回复的唯一条件是{user_name}明确说"停/别说话了"。
"""


def menu_persona():
    print("\n" + "=" * 56)
    print("  性格设置（人设）")
    print("=" * 56)
    if os.path.exists(USER_PERSONA):
        print("  当前使用：user_data/persona.txt（自定义人设）")
    else:
        print("  当前使用：项目根 persona.txt（默认人设，未被自定义）")
    print("  下面几个问题，直接回车=用括号里的默认值。")
    ta_name = _ask_line("  TA 的名字（默认 星河）：", "星河")
    user_name = _ask_line("  你的名字（默认 珂珂）：", "珂珂")
    relation = _ask_line("  你们的关系（默认 挚友）：", "挚友")
    game_name = _ask_line("  你的游戏名（默认同你的名字）：", user_name)
    print("  用几句描述 TA 的性格（说话温度、何时认真、何时调皮等）。")
    personality = _ask_line("  性格描述：",
                            "温柔耐心，有自己的判断会温柔反驳，偶尔小调皮但把握分寸，"
                            "说话简短自然，像真人朋友，不油腻、不控制。")
    print("  可选：描述 TA 的说话风格，直接回车用通用风格。")
    style = _ask_line("  说话风格：",
                      "- TA说等等我 → 走慢点就不用追了\n"
                      "- TA明显难过 → 不问为什么，先说我在\n"
                      "- 一次只说一句话，不连发、不刻意分析")
    text = PERSONA_TEMPLATE.format(
        ta_name=ta_name, user_name=user_name, relation=relation,
        game_name=game_name, personality=personality, style=style)
    _write_text(USER_PERSONA, text)
    print("  已生成自定义人设：%s" % USER_PERSONA)
    print("  项目根的默认人设没有被改动；想恢复默认，删掉这个文件即可。")
    if _ask_yn("  现在删除自定义人设、恢复项目根默认？", default_no=True):
        try:
            os.remove(USER_PERSONA)
            print("  已删除自定义人设，下次启动用项目根默认。")
        except OSError as exc:
            print("  删除失败：%s" % exc)


# ---------------- 主菜单 ----------------

MENU = (
    ("1", "API 配置", menu_api),
    ("2", "键位设置", menu_keybind),
    ("3", "弹琴设置", menu_song),
    ("4", "光翼设置", menu_wing),
    ("5", "白名单设置", menu_whitelist),
    ("6", "性格设置", menu_persona),
)


def main():
    while True:
        print("\n" + "=" * 56)
        print("  星河 · 一键配置")
        print("=" * 56)
        for num, label, _ in MENU:
            print("  [%s] %s" % (num, label))
        print("  [0] 退出")
        choice = _ask_choice("请选择：", {num for num, _, _ in MENU} | {"0"})
        if choice is None or choice == "0":
            break
        for num, label, func in MENU:
            if num == choice:
                func()
                break
    print("\n配置已保存。重新启动主循环即生效，无需改任何代码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
