# -*- coding: utf-8 -*-
"""用 deepseek-flash（原生多模态）识别现有光遇截图，验证"能看见"的效果与成本。
不依赖真机、不连游戏，直接读本地图片。

用法：
  py test_deepseek_vision.py            # 跑默认的几张代表性截图
  py test_deepseek_vision.py 图1.png 图2.jpg
"""
import base64
import json
import os
import sys
import time

import requests

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"

# 项目根目录（本脚本在项目根目录）
ROOT = os.path.dirname(os.path.abspath(__file__))

# 默认测试图（存在才用）：聊天面板 / 好友树按钮 / 场景 / 面板翻转存证
DEFAULT_IMAGES = [
    r"D:\光遇截图\光遇聊天框\sky_20260825_011257_717.png",
    r"D:\光遇截图\好友树打开按钮\sky_20260822_200327_968.png",
    os.path.join(ROOT, "ocr_real_shots", "real1_grass.png"),
    os.path.join(ROOT, "flip_debug", "chat_225728_on_b100_t3.jpg"),
]

QUESTION = (
    "这是游戏《光·遇》PC版的截图。请仔细观察，只返回JSON、不要多余文字：\n"
    '{"scene": "判断这是哪里（遇境/云野/雨林/霞谷/墓土/禁阁等，不确定写unknown）",'
    ' "panels": ["出现的面板/界面，如聊天面板/好友树/设置/确认弹窗，没有则空数组"],'
    ' "icons": ["看到的可交互图标，如牵手/点火/坐下/打开好友树/传送，没有则空数组"],'
    ' "pose": "画面中靠近中央、头顶无名字的自己角色的姿势 standing/crouching/sitting/lying",'
    ' "chat": "若有聊天文字，简要列出最近几条（含说话人），没有则空字符串",'
    ' "note": "其他值得注意的一句话"}'
)


def load_key():
    k = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "")
    if k:
        return k.strip()
    with open(os.path.join(ROOT, "key.txt"), "r", encoding="utf-8") as f:
        return f.read().strip()


def encode_image(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = "jpeg" if ext in ("jpg", "jpeg") else ("png" if ext == "png" else ext)
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/{mime};base64,{b64}"


def recognize(key, path):
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": QUESTION},
                {"type": "image_url", "image_url": {"url": encode_image(path)}},
            ],
        }],
        "max_tokens": 500,
        "temperature": 0.1,
        # 关闭思考：我们要的是结构化 JSON，思考会吃光 token 导致 content 为空
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    t0 = time.time()
    r = requests.post(BASE_URL + "/chat/completions", headers=headers,
                      json=payload, timeout=60)
    dt = time.time() - t0
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}", dt
    data = r.json()
    text = data["choices"][0]["message"].get("content", "")
    usage = data.get("usage", {})
    return text, usage, dt


def main():
    paths = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_IMAGES
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        print("没有找到可用的测试图片")
        return 1
    key = load_key()
    print(f"模型: {MODEL}（原生多模态）  测试 {len(paths)} 张图\n")

    tot_in = tot_out = 0
    for i, p in enumerate(paths, 1):
        print(f"[{i}/{len(paths)}] {os.path.basename(p)}")
        text, usage, dt = recognize(key, p)
        if text is None:
            print(f"  失败: {usage}\n")
            continue
        try:
            obj = json.loads(text.strip().strip("`").replace("json\n", "", 1)
                             if text.strip().startswith("```") else text)
        except Exception:
            # 容错：再尝试抠 { ... }
            s = text[text.find("{"):text.rfind("}") + 1]
            try:
                obj = json.loads(s)
            except Exception:
                obj = None
        if isinstance(obj, dict):
            for k, v in obj.items():
                print(f"  {k}: {v}")
        else:
            print(f"  原始返回: {text[:400]}")
        pin = usage.get("prompt_tokens", 0)
        pout = usage.get("completion_tokens", 0)
        tot_in += pin
        tot_out += pout
        print(f"  -- token 入={pin} 出={pout} 耗时={dt:.1f}s\n")

    print("=" * 50)
    print(f"合计 token: 入={tot_in} 出={tot_out}（{len(paths)} 张图）")
    print("每张图图片部分 token 上限 1024，大图自动缩放")
    return 0


if __name__ == "__main__":
    sys.exit(main())
