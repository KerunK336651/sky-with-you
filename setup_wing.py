# -*- coding: utf-8 -*-
"""光翼自动拾取设置向导（双击 setup_wing.bat 或直接 py setup_wing.py）。

只问两个问题，写入 user_data/settings.json（本地文件，已 gitignore，不进仓库）：
  ① 这个号是不是有翼号？（默认：无翼 —— 最保守）
  ② 要不要开启靠近光翼自动拾取？（默认：关闭）

**两把钥匙都到位才生效**：有翼号 + 明确开启。
只设环境变量 SKY_AUTO_PICKUP_WING=1 但账号没标记有翼 → 主程序强制关闭并告警。

为什么这么设计：无翼角色一旦拾取光翼就再也回不到无翼，不可逆。项目已开源，
别人 clone 下来的默认值必须是「有翼=否 + 拾取=关」。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.wing_settings import (  # noqa: E402
    SETTINGS_FILE, load_settings, save_settings, resolve_pickup, describe,
)


def _ask_yn(prompt: str, default_no: bool = True) -> bool:
    hint = "[y/N]" if default_no else "[Y/n]"
    while True:
        try:
            raw = input(f"{prompt} {hint}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return not default_no if False else False
        if not raw:
            return not default_no if default_no else True
        if raw in ("y", "yes", "是", "1"):
            return True
        if raw in ("n", "no", "否", "0"):
            return False
        print("  输入 y 或 n（回车 = %s）" % ("否" if default_no else "是"))


def main() -> int:
    cur = load_settings()
    print("=" * 62)
    print("  光翼自动拾取 —— 设置向导")
    print("=" * 62)
    print("  当前设置：%s" % describe(cur))
    print("  设置文件：%s" % SETTINGS_FILE)
    print()
    print("  注意：无翼号（永久无翼）一旦拾取光翼【不可逆】，必须保持关闭。")
    print("        本向导默认值全部按最保守来（有翼=否、拾取=关）。")
    print()

    winged = _ask_yn("① 这个号是【有翼号】吗？（选 n = 按无翼号处理，最安全）", default_no=True)

    if winged:
        pickup = _ask_yn("② 要不要开启【靠近光翼自动拾取】？", default_no=True)
    else:
        print("  无翼号 → 自动拾取强制关闭（设了也不会按 F）。")
        pickup = False

    new = save_settings({"account_winged": winged, "auto_pickup_wing": pickup})
    enabled, reason = resolve_pickup(os.environ.get("SKY_AUTO_PICKUP_WING", ""), new)

    print()
    print("-" * 62)
    print("  已保存：%s" % describe(new))
    print("  生效判定：自动拾取=%s（%s）" % ("开启" if enabled else "关闭", reason))
    print("  下次启动主程序即生效；随时可以再跑一次本向导改回来。")
    print("-" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
