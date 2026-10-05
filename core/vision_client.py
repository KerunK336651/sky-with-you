# -*- coding: utf-8 -*-
"""VLM 视觉客户端（低频“视觉顾问”，不进主循环）。

定位：本地 YOLO/RapidOCR 负责高频、确定的感知（面板/输入框/牵手图标/聊天文字）；
本模块只在关键节点低频调用云端视觉大模型，补本地做不到的语义判断，例如：
  - 姿势动作结果校验（按完 坐下/躺下 后，看一眼角色到底是站/蹲/坐/躺）
  - 好友树展开后识别互动节点与焦点（阶段2）

设计原则：
  - 默认关闭（SKY_VISION_ENABLED=1 才启用）；没 key / 关闭 / 任何网络或解析异常，
    一律返回 None，由调用方回退到现有“盲信按键成功”逻辑，绝不拖垮主循环。
  - 走 OpenAI 兼容 /chat/completions（默认阿里云百炼 qwen-vl-plus，可换豆包等）。
  - 截图先缩到 max_width 再发，控制 token 与延迟；只发当前帧，不依赖模型记忆。
"""
import base64
import json
import os
import re
import time

try:
    import requests
except Exception:  # requests 理论上在依赖里，缺了也不让 import 崩
    requests = None

try:
    import cv2
except Exception:
    cv2 = None


# ───────────────────────── 纯函数（离线可单测） ─────────────────────────

def strip_json_block(text):
    """从模型输出里抠出 JSON 对象：去掉 ```json 代码块围栏、前后说明文字。
    找不到合法对象时返回 None。"""
    if not text:
        return None
    t = text.strip()
    # 去 ```json ... ``` 或 ``` ... ``` 围栏
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    # 截取第一个 { 到最后一个 }，容忍前后啰嗦
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        t = t[i:j + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


_POSE_WORDS = [
    ("lying", ("lying", "lie", "lay", "躺")),
    ("sitting", ("sitting", "sit", "sat", "坐")),
    ("crouching", ("crouching", "crouch", "squat", "蹲")),
    ("standing", ("standing", "stand", "stood", "站")),
]


def normalize_pose(value):
    """把模型返回的中文/英文姿势归一到 standing/crouching/sitting/lying；
    无法判断返回 None。匹配按 躺>坐>蹲>站 的具体度顺序，避免“坐着”被“站”误吞。"""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    for canon, words in _POSE_WORDS:
        if any(w in s for w in words):
            return canon
    return None


def pose_press_delta(cur_idx, target_idx, cycle=4):
    """姿势是 cycle 次一循环（站0->蹲1->坐2->躺3->站0），返回从当前到目标还要按几下。"""
    return (int(target_idx) - int(cur_idx)) % cycle


# ─────────────────────────────── 客户端 ────────────────────────────────

class VisionClient:
    def __init__(self, enabled=None, api_key="", base_url="", model="",
                 timeout=20.0, max_width=1280, jpeg_quality=80, pose_crop=None):
        if enabled is None:
            enabled = os.environ.get("SKY_VISION_ENABLED", "0") == "1"
        self.enabled = bool(enabled)
        # 视觉提供方：deepseek（复用 DeepSeek key，deepseek-flash 原生多模态）
        # 或默认 qwen（阿里云百炼 qwen-vl-plus）。用 SKY_VISION_PROVIDER 切换。
        self.provider = os.environ.get("SKY_VISION_PROVIDER", "qwen").lower().strip()
        if self.provider == "deepseek":
            default_base = "https://api.deepseek.com"
            default_model = "deepseek-flash"
        else:
            default_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            default_model = "qwen-vl-plus"
        self.base_url = (base_url or os.environ.get(
            "SKY_VISION_BASE_URL", default_base)).rstrip("/")
        self.model = model or os.environ.get("SKY_VISION_MODEL", default_model)
        self.api_key = api_key or self._load_key()
        self.timeout = float(timeout)
        self.max_width = int(max_width)
        self.jpeg_quality = int(jpeg_quality)
        # 姿势判断默认用【完整帧】。实测（2026-10-05）：裁剪放大后，站在画面中央的
        # 别人会强烈干扰模型，即使它“描述”时认对了，POSE_QUESTION 仍常把中央站着的
        # 别人误当成自己、判错姿势；完整帧反而 6/6 稳定判对。如需裁剪，可用
        # SKY_VISION_POSE_CROP=x0,y0,x1,y1（比例）覆盖。
        if pose_crop is None:
            env = os.environ.get("SKY_VISION_POSE_CROP", "").strip()
            if env:
                try:
                    pose_crop = tuple(float(x) for x in env.split(","))
                except Exception:
                    pose_crop = None
            else:
                pose_crop = None
        self.pose_crop = pose_crop
        self.total_calls = 0
        self.total_fail = 0

    @staticmethod
    def _project_root():
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _load_key(self):
        # 优先视觉专用 key
        k = os.environ.get("SKY_VISION_API_KEY", "")
        if k:
            return k.strip()
        if self.provider == "deepseek":
            # 复用 DeepSeek 主 key：环境变量或项目根目录 key.txt
            k = (os.environ.get("DEEPSEEK_API_KEY", "")
                 or os.environ.get("OPENROUTER_API_KEY", ""))
            if k:
                return k.strip()
            p = os.path.join(self._project_root(), "key.txt")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except OSError:
                return ""
        # qwen：兼容复用文本 key，或读 vision_key.txt
        k = os.environ.get("OPENROUTER_API_KEY", "")
        if k:
            return k.strip()
        p = os.path.join(self._project_root(), "vision_key.txt")
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    @property
    def available(self):
        """启用、有 key、依赖齐全才算可用。"""
        return bool(self.enabled and self.api_key and requests is not None)

    def encode_frame(self, frame, crop=None):
        """BGR ndarray -> data:image/jpeg;base64,...；crop=(x0,y0,x1,y1)比例裁剪；失败返回 None。"""
        if frame is None or cv2 is None:
            return None
        try:
            img = frame
            h, w = img.shape[:2]
            if crop and len(crop) == 4:
                x0 = max(0, int(w * crop[0])); x1 = min(w, int(w * crop[2]))
                y0 = max(0, int(h * crop[1])); y1 = min(h, int(h * crop[3]))
                if x1 > x0 and y1 > y0:
                    img = img[y0:y1, x0:x1]
                    h, w = img.shape[:2]
            if w > self.max_width:
                nh = max(1, int(h * self.max_width / w))
                img = cv2.resize(img, (self.max_width, nh))
            ok, buf = cv2.imencode(".jpg", img,
                                  [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ok:
                return None
            return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
        except Exception:
            return None

    def ask(self, frame, question, max_tokens=300, temperature=0.1, crop=None):
        """发一帧+问题，返回模型文本；不可用或任何异常返回 None（安全回退）。"""
        if not self.available:
            return None
        data_url = self.encode_frame(frame, crop=crop)
        if not data_url:
            return None
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # DeepSeek flash 关闭思考，避免 token 全花在推理上、content 为空
        if self.provider == "deepseek" or "deepseek" in self.base_url:
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": "Bearer " + self.api_key,
                   "Content-Type": "application/json"}
        t0 = time.time()
        self.total_calls += 1
        try:
            r = requests.post(self.base_url + "/chat/completions",
                              headers=headers, json=payload, timeout=self.timeout)
            if r.status_code != 200:
                self.total_fail += 1
                print(f"  [Vision] HTTP {r.status_code}: {r.text[:160]}")
                return None
            data = r.json()
            text = data["choices"][0]["message"].get("content", "")
            usage = data.get("usage", {})
            print(f"  [Vision] {time.time()-t0:.1f}s "
                  f"in={usage.get('prompt_tokens','?')} out={usage.get('completion_tokens','?')}")
            return text
        except Exception as e:
            self.total_fail += 1
            print(f"  [Vision] 调用异常(已回退): {type(e).__name__}: {str(e)[:120]}")
            return None

    def ask_json(self, frame, question, max_tokens=300, crop=None):
        """发问题并解析成 dict，失败/非 JSON 返回 None。"""
        txt = self.ask(frame, question, max_tokens=max_tokens, crop=crop)
        if txt is None:
            return None
        obj = strip_json_block(txt)
        if obj is None:
            print(f"  [Vision] 返回无法解析为JSON: {txt[:120]}")
        return obj

    # ── 具体任务1：姿势校验 ──────────────────────────────────────────
    POSE_QUESTION = (
        "这是游戏《光·遇》的画面（已裁出画面中下部，画面里可能同时有多个斗篷小人）。\n"
        "【严格按下面的顺序判定，不要颠倒】：\n"
        "1. 先逐个看小人头顶：头顶【有】中文名字标签（例如珂珂/幺幺/阿颜）的，100% 是其他玩家，"
        "立刻把他们排除，绝不允许把他们的姿势当答案；\n"
        "2. 你自己操作的角色，头顶【永远没有】任何名字标签。在头顶【完全没有名字】的小人里，"
        "挑最靠近画面中央偏下的那个，那就是你自己（‘靠近中央’只是辅助，前提是它头顶确实没有名字）；\n"
        "3. 只判断这个‘头顶无名字’的自己角色当前的身体姿势，"
        "只返回JSON、不要多余文字：\n"
        '{"pose": "standing或crouching或sitting或lying", "sure": true或false}\n'
        "standing=站着, crouching=蹲着/屈膝, sitting=坐在地上/凳子, lying=躺下。\n"
        "若所有小人头顶都有名字、头顶无名字的小人有多个无法区分、或角色被挡住/移动模糊，"
        "就 sure=false。"
    )

    def classify_pose(self, frame):
        """返回 (pose, sure)；不可用/不确定返回 (None, False)。"""
        obj = self.ask_json(frame, self.POSE_QUESTION, max_tokens=120,
                            crop=self.pose_crop)
        if not isinstance(obj, dict):
            return None, False
        pose = normalize_pose(obj.get("pose"))
        sure = bool(obj.get("sure", True))
        if pose is None:
            return None, False
        return pose, sure
