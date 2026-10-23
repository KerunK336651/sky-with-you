# -*- coding: utf-8 -*-
"""
yolo_preview.py — 独立 YOLO 实时预览窗口（纯查看，绝不按键）

做的事：抓光遇游戏画面 -> 跑 YOLO -> 画框弹窗。
不连 MCP / Arduino / AI / 聊天 OCR，不发送任何游戏按键，挂机调试最安全。
双击 yolo_preview.bat 启动；窗口内按 q / ESC 或点 × 退出。

困难负样本采集（为 v5 重训准备）：
  游戏里看到“圆形 F 但不是互动”的图标被误检（星盘/传送/衣柜/纸船/光翼/音符…），
  按 F9 即可把当前【无框原始分辨率帧】存进困难负样本文件夹（不画框、不缩放）。
  F9 用全局低级键盘钩子，游戏在前台也能捕获，且按键照常放行给游戏。

环境变量（可选）：
  SKY_YOLO_MODEL              模型路径（默认 22 类 v6 best.pt）
  SKY_YOLO_CONF               置信度阈值（默认 0.80）
  SKY_YOLO_PREVIEW_SCALE      显示缩放（默认 0.67）
  SKY_YOLO_PREVIEW_INTERVAL   推理间隔秒（默认 0.10，越小越吃 CPU）
  SKY_NEG_DIR                 困难负样本保存目录（默认 D:\\光遇互动采集\\hard_negative）
"""
import os
import sys
import time
import threading

import numpy as np
import cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

MODEL_PATH = os.environ.get(
    "SKY_YOLO_MODEL",
    os.path.join(ROOT, "runs", "detect", "sky_v6_22cls", "weights", "best.pt"))
CONF = float(os.environ.get("SKY_YOLO_CONF", "0.80"))
SCALE = float(os.environ.get("SKY_YOLO_PREVIEW_SCALE", "0.67"))
INTERVAL = float(os.environ.get("SKY_YOLO_PREVIEW_INTERVAL", "0.10"))
WIN = "Sky YOLO Preview  (q/ESC=quit)"

VK_F9 = 0x78  # 采集困难负样本
SAVE_DEBOUNCE = 0.5  # F9 防抖秒，防按住连拍


def _default_neg_dir():
    env = os.environ.get("SKY_NEG_DIR")
    if env:
        return env
    if os.path.isdir("D:\\"):
        return r"D:\光遇互动采集\hard_negative"
    return os.path.join(ROOT, "hard_negatives")


NEG_DIR = _default_neg_dir()


def count_existing_neg():
    """启动时统计已存困难负样本张数，让 HUD 计数连续。"""
    try:
        if os.path.isdir(NEG_DIR):
            return len([x for x in os.listdir(NEG_DIR)
                        if x.startswith("hardneg_") and x.lower().endswith(".png")])
    except Exception:
        pass
    return 0


def save_negative(frame, idx):
    """存【原始无框帧】为 PNG（中文路径用 imencode），返回文件名或 None。"""
    try:
        os.makedirs(NEG_DIR, exist_ok=True)
        fn = os.path.join(NEG_DIR, "hardneg_%s_%04d.png" % (
            time.strftime("%Y%m%d_%H%M%S"), idx))
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return None
        with open(fn, "wb") as f:
            f.write(buf.tobytes())
        return fn
    except Exception as e:
        print("[F9] 保存失败：%s" % e)
        return None

CN = {0: "聊天框", 1: "牵手", 2: "好友树", 3: "抱抱", 4: "背背", 5: "击掌",
      6: "碰拳", 7: "跟随", 8: "光翼", 9: "点火", 10: "聚焦", 11: "打坐",
      12: "熊抱", 13: "谢幕", 14: "花式击掌", 15: "握手", 16: "摸头",
      17: "互火", 18: "打闹", 19: "搭肩", 20: "共舞", 21: "耳语"}
COLORS = {0: (170, 170, 170), 1: (0, 210, 0), 2: (255, 160, 0),
          3: (200, 0, 200), 4: (0, 150, 255), 5: (255, 200, 0),
          6: (60, 60, 255), 7: (255, 80, 180), 8: (0, 0, 255),
          9: (0, 180, 255), 10: (200, 200, 0), 11: (0, 220, 220),
          12: (180, 0, 120), 13: (128, 0, 128), 14: (0, 200, 100),
          15: (160, 120, 0), 16: (200, 100, 255), 17: (0, 100, 255),
          18: (0, 0, 180), 19: (100, 100, 100), 20: (255, 0, 128),
          21: (180, 200, 0)}


def draw_frame(frame, boxes, names, font, fps, backend, neg_count=0, flash=False):
    """在一帧上画框 + 中文标签 + 顶部 HUD，返回显示用 BGR 图。"""
    img = frame.copy()
    h, w = img.shape[:2]
    pil_ok = False
    if font is not None:
        try:
            from PIL import Image, ImageDraw
            im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            d = ImageDraw.Draw(im)
            for cls, cf, (x1, y1, x2, y2) in boxes:
                bgr = COLORS.get(cls, (255, 255, 255))
                rgb = (bgr[2], bgr[1], bgr[0])
                d.rectangle([x1, y1, x2, y2], outline=rgb, width=3)
                label = "%s %.2f" % (CN.get(cls, names.get(cls, str(cls))), cf)
                tb = d.textbbox((x1, y1), label, font=font)
                tw, th = tb[2] - tb[0], tb[3] - tb[1]
                ly = max(0, int(y1) - th - 4)
                d.rectangle([x1, ly, x1 + tw + 8, ly + th + 4], fill=rgb)
                d.text((x1 + 4, ly + 1), label, fill=(255, 255, 255), font=font)
            img = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
            pil_ok = True
        except Exception:
            pil_ok = False
    if not pil_ok:
        for cls, cf, (x1, y1, x2, y2) in boxes:
            bgr = COLORS.get(cls, (255, 255, 255))
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), bgr, 2)
            cv2.putText(img, "%s %.2f" % (names.get(cls, cls), cf),
                        (int(x1), max(15, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, bgr, 2)
    line1 = "YOLO conf>=%.2f | boxes:%d | fps:%.1f | backend:%s | hardneg:%d" % (
        CONF, len(boxes), fps, backend, neg_count)
    if flash:
        line2 = "F9 SAVED hard-negative #%d (raw frame)" % neg_count
        col2 = (0, 255, 0)
    else:
        line2 = "F9 = save raw frame as hard-negative | q/ESC = quit"
        col2 = (200, 200, 200)
    cv2.rectangle(img, (0, 0), (w, 60), (0, 0, 0), -1)
    cv2.putText(img, line1, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (255, 255, 255), 2)
    cv2.putText(img, line2, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                col2, 2)
    if SCALE and SCALE != 1.0:
        img = cv2.resize(img, None, fx=SCALE, fy=SCALE)
    return img


def main():
    import queue
    from collect_interactions import (
        find_game_window, ScreenGrabber, CaptureThread, start_keyboard_hook)

    region = find_game_window()
    if not region:
        print("[错误] 没找到“光·遇”游戏窗口，请先进入游戏（出现角色画面）后再运行本窗口。")
        try:
            input("按回车键退出...")
        except Exception:
            pass
        return

    grabber = ScreenGrabber()
    print("=" * 60)
    print("  独立 YOLO 预览（纯查看，不会按键）")
    print("  截图后端：%s %s" % (grabber.backend, grabber.mod_name or ""))
    print("=" * 60)
    cap = CaptureThread(grabber)
    cap.start()

    t0 = time.time()
    while cap.get_latest() is None and time.time() - t0 < 5:
        time.sleep(0.1)
    frame0 = cap.get_latest()
    if frame0 is None:
        print("[错误] 抓不到游戏画面。若游戏是全屏独占，请改成无边框窗口，或装 bettercam。")
        cap.stop()
        return

    from ultralytics import YOLO
    if not os.path.exists(MODEL_PATH):
        print("[错误] 模型不存在：%s" % MODEL_PATH)
        cap.stop()
        return
    print("加载模型：%s" % MODEL_PATH)
    model = YOLO(MODEL_PATH)
    names = model.names
    print("类别：%s" % names)

    font = None
    try:
        from PIL import ImageFont
        h0 = frame0.shape[0]
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", max(20, int(h0 * 0.022)))
    except Exception as e:
        print("（中文字体不可用，标签降级英文：%s）" % e)

    state = {"boxes": []}
    running = {"v": True}

    def infer_loop():
        last = 0.0
        while running["v"]:
            frame = cap.get_latest()
            if frame is None:
                time.sleep(0.02)
                continue
            if time.time() - last < INTERVAL:
                time.sleep(0.01)
                continue
            try:
                r = model(frame, conf=CONF, verbose=False)[0]
                state["boxes"] = [
                    (int(b.cls[0]), float(b.conf[0]), tuple(map(float, b.xyxy[0])))
                    for b in r.boxes]
            except Exception:
                pass
            last = time.time()

    th = threading.Thread(target=infer_loop, name="YOLO-infer", daemon=True)
    th.start()

    # 全局键盘钩子：游戏在前台也能捕获 F9（钩子永远放行，不拦截游戏按键）
    key_q = queue.Queue()
    hook_t, hook_cb = start_keyboard_hook(key_q)
    neg_count = count_existing_neg()
    last_save = 0.0
    flash_until = 0.0

    print("预览窗口已打开：窗口里按 q / ESC 退出（只看，不会按键）。")
    print("困难负样本：看到误检按 F9 存【原始无框帧】 -> %s（已有 %d 张）" % (
        NEG_DIR, neg_count))
    fps, fps_t, fps_n = 0.0, time.time(), 0
    try:
        while True:
            frame = cap.get_latest()
            if frame is None:
                time.sleep(0.02)
                continue
            now = time.time()
            fps_n += 1
            if now - fps_t >= 1.0:
                fps = fps_n / (now - fps_t)
                fps_t, fps_n = now, 0

            # 处理 F9 存困难负样本（全局钩子，非阻塞取键）
            try:
                while True:
                    vk = key_q.get_nowait()
                    if vk == VK_F9 and now - last_save >= SAVE_DEBOUNCE:
                        fn = save_negative(frame, neg_count + 1)
                        if fn:
                            neg_count += 1
                            last_save = now
                            flash_until = now + 1.5
                            print("[F9] 已存困难负样本 #%d -> %s" % (neg_count, fn))
            except queue.Empty:
                pass
            except Exception:
                pass

            img = draw_frame(frame, state["boxes"], names, font, fps,
                             grabber.backend, neg_count, now < flash_until)
            cv2.imshow(WIN, img)
            k = cv2.waitKey(20) & 0xFF
            if k in (ord('q'), ord('Q'), 27):
                break
            try:
                if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except Exception:
                pass
    finally:
        running["v"] = False
        cap.stop()
        cv2.destroyAllWindows()
        print("预览已退出。")


if __name__ == "__main__":
    main()
