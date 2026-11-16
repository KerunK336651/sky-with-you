# -*- coding: utf-8 -*-
"""弹琴设置向导（玩家自己填，不用改代码）。

解决的问题：1~8 快捷栏摆位每个人都不一样，乐器类型也不一样。
开源给别人用时不能写死，也不能让不会改代码的人去编辑 json。
这个向导只改 `user_data/song_settings.json`（本地文件，不进仓库）。

用法：双击 setup_song.bat（或 `py setup_song.py`）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.song_settings import (  # noqa: E402
    INSTRUMENT_KINDS, KIND_LABELS, SETTINGS_FILE,
    load_settings, save_settings, describe,
)

KIND_ORDER = ("piano", "violin", "flute", "harmonica")


def _ask(prompt, valid):
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            return None
        if raw == "":
            print("  （直接回车 = 不改，保持原样）")
            return None
        if raw in valid:
            return raw
        print(f"  输入无效，可选项：{' / '.join(sorted(valid))}")


def _set_slot(cur):
    print("\n快捷栏槽位 = 游戏里能打开乐器界面的那个数字键（1~8）。")
    print("不确定就进游戏挨个按 1~8，看哪个打开乐器界面，记住它再来填。")
    val = _ask("请输入 1~8（输入 0 = 清空为未设置）：",
               {str(i) for i in range(0, 9)})
    if val is None:
        return cur
    slot = int(val)
    cur = save_settings({"instrument_slot": slot})
    print("  已保存：%s" % ("清空（未设置）" if slot == 0 else f"快捷栏 {slot}"))
    return cur


def _set_kind(cur):
    print("\n当前背的乐器（决定能不能长按）：")
    for i, k in enumerate(KIND_ORDER, 1):
        print(f"  {i}) {KIND_LABELS[k]}")
    print("  说明：钢琴按住不延音；小提琴/笛子/口琴可以拉长音。")
    val = _ask("请选择 1~4（输入 0 = 保持默认钢琴）：",
               {str(i) for i in range(0, 5)})
    if val is None:
        return cur
    kind = "piano" if val == "0" else KIND_ORDER[int(val) - 1]
    cur = save_settings({"instrument_kind": kind})
    print(f"  已保存：{KIND_LABELS.get(kind, kind)}")
    return cur


def main():
    print("=" * 56)
    print("  星河 · 弹琴设置")
    print("=" * 56)
    print("配置文件：%s" % SETTINGS_FILE)
    cur = load_settings()
    while True:
        print("\n当前设置：%s" % describe(cur))
        print("  [1] 设置快捷栏槽位（1~8）")
        print("  [2] 设置乐器类型")
        print("  [3] 清空快捷栏（设为未设置）")
        print("  [0] 退出")
        choice = _ask("请选择：", {"0", "1", "2", "3"})
        if choice is None or choice == "0":
            break
        if choice == "1":
            cur = _set_slot(cur)
        elif choice == "2":
            cur = _set_kind(cur)
        elif choice == "3":
            cur = save_settings({"instrument_slot": 0})
            print("  已清空：快捷栏未设置（此时星河不会自己弹琴，会提示你来设置）")
        cur = load_settings()

    print("\n最终设置：%s" % describe(load_settings()))
    print("提示：改完直接重新启动主循环即可生效（无需改任何代码）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
