# -*- coding: utf-8 -*-
"""
负样本采集工具
按F9截取当前屏幕，保存到 negatives/ 目录
按F12退出

采集场景建议（每种5-10张）：
1. 先祖星盘 / 好友星盘
2. 好友树界面（展开的动作树）
3. 设置界面（右上角设置按钮）
4. 回遇境确认面板
5. 表情快捷栏（按E打开）
6. 衣柜界面
7. 地图神坛
8. 普通游戏画面（无UI）
9. 聊天面板关闭时的画面
10. 传送/确认/取消等弹窗
"""
import os
import time
import cv2
import mss
import numpy as np
from datetime import datetime

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "negatives")
os.makedirs(SAVE_DIR, exist_ok=True)

# 游戏窗口客户区（和主程序一致）
GAME_REGION = {"left": 1216, "top": 171, "width": 1264, "height": 681}

def main():
    print("=" * 50)
    print("  YOLO 负样本采集工具")
    print("  F9  = 截取当前画面")
    print("  F12 = 退出")
    print("=" * 50)
    print(f"保存目录: {SAVE_DIR}")
    print()
    print("请在游戏中切换到以下界面后按F9:")
    print("  1. 先祖星盘 / 好友星盘")
    print("  2. 好友树界面")
    print("  3. 设置界面")
    print("  4. 回遇境确认面板")
    print("  5. 表情快捷栏(按E)")
    print("  6. 衣柜界面")
    print("  7. 地图神坛")
    print("  8. 普通游戏画面(无UI)")
    print("  9. 聊天面板关闭时")
    print("  10. 各种弹窗(传送/确认/取消)")
    print()

    sct = mss.mss()
    count = len([f for f in os.listdir(SAVE_DIR) if f.endswith('.png')])
    print(f"已有 {count} 张负样本")
    print()

    try:
        while True:
            if cv2.waitKey(1) & 0xFF == 0:
                pass

            # 检测F9
            import ctypes
            GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
            if GetAsyncKeyState(0x78) & 0x8000:  # F9
                ts = datetime.now().strftime("%H%M%S")
                fname = f"neg_{ts}_{count+1:04d}.png"
                fpath = os.path.join(SAVE_DIR, fname)

                img = np.array(sct.grab(GAME_REGION))
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                cv2.imwrite(fpath, img)
                count += 1
                print(f"  [{count}] 已保存: {fname}")
                time.sleep(0.3)

            if GetAsyncKeyState(0x7B) & 0x8000:  # F12
                break

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass

    print(f"\n完成，共采集 {count} 张负样本")
    print(f"保存在: {SAVE_DIR}")
    print("运行 python add_negatives.py 将负样本加入训练集")

if __name__ == "__main__":
    main()
