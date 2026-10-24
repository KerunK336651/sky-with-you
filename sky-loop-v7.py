# sky-loop-v7.py — 光遇循环调度器 v7.1 (sky-companion 聊天引擎移植版)
#
# v7.0 → v7.1（聊天引擎大换血）:
#   - 集成 sky-companion 的聊天引擎能力：
#     * 长期记忆系统（自动整理对话为长期理解提示词）
#     * 联网搜索 + 知识持久化（无Key多引擎搜索）
#     * 风格学习（人设关键词触发联网搜风格参考）
#     * 回复打磨（去笑声口癖）+ 防重复检测 + 自动重写
#     * 固定回复兜底（在吗/你是谁/为什么卡等）
#     * 统一 LLM 客户端（支持 OpenRouter 和 DeepSeek 直连 HTTP）
#   - 保留 v7.0 的全部优势：
#     * PanelDetector 感知层（三路融合 + WorldState）
#     * 四线程架构（Watch/OCR/AI/Action）
#     * MCP 执行层 + Arduino 硬件键盘
#     * 丰富游戏动作（点火/收火/鞠躬/牵手/回家/弹窗确认）
#     * [CHAT]/[ACT]/[KEY]/[IDLE] 回复协议
#     * persona.txt 人设机制
#
# 启动:
#   1. 先开 MCP:  python sky-mcp-server.py --http --port 9900 --token 1234
#   2. 再开本程序: python sky-loop-v7.py
# -*- coding: utf-8 -*-

import os
import shutil
# 限住推理库的线程数，必须在 onnxruntime/numpy 加载前设置。
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

import openai
import requests
import json
import time
import re
import sys
import threading
import queue
from difflib import SequenceMatcher
import numpy as np
import cv2
from rapidocr_onnxruntime import RapidOCR

from panel_detector import (PanelDetector, DetectorConfig, Screen,
                            find_game_client_region, make_ocr_engine, ocr_infer)

# ── 移植自 sky-companion 的核心模块 ──
from core.llm_client import LLMClient
from core.memory import (
    load_memory, add_turn, memory_prompt, needs_update, update_profile,
    companion_replies, memory_pending_turns, build_memory_update_prompt,
    clean_memory_summary, load_search_knowledge, add_search_knowledge,
    search_knowledge_prompt, load_style_knowledge, save_style_knowledge,
    diary_recent, diary_recall, add_diary_entry,
    _atomic_write_json, _load_json, USER_DATA_DIR,
    weekly_recent, monthly_recent, yearly_recent, summary_recall,
    summarize_due_periods, due_periods,
)
from core.web_search import search_web, format_results
from core.reply_engine import (
    needs_web_search, build_search_query, filter_search_results,
    polish_reply, reply_too_similar, rewrite_repetitive_reply,
    recent_laugh_count, fallback_reply, must_reply, answer_from_search,
)
from core.style_learner import learn_style, style_prompt_key, player_style_profile
from core.vision_client import VisionClient, pose_press_delta

# 本地知识库（可选；缺失时不影响主流程）
try:
    import knowledge
except Exception as _kb_imp_err:
    knowledge = None
    print("  [Knowledge] 模块不可用: %s" % _kb_imp_err)

# 输出编码跟随系统默认（Windows GBK），避免 cmd 下乱码
# 如需 UTF-8 输出，设置环境变量 PYTHONIOENCODING=utf-8 并 chcp 65001

# ===================== 配置 =====================

MCP_URL       = "http://127.0.0.1:9900"
MCP_TOKEN     = "1234"

def _load_api_key():
    """优先环境变量，其次脚本同目录的 key.txt"""
    k = os.environ.get("OPENROUTER_API_KEY", "")
    if k:
        return k
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "key.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

API_KEY       = _load_api_key()

# LLM provider: "openrouter" (默认, OpenAI SDK) 或 "deepseek" (直连 HTTP)
LLM_PROVIDER  = os.environ.get("SKY_LLM_PROVIDER", "openrouter").lower().strip()
if LLM_PROVIDER == "deepseek":
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-flash"
else:
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

MODEL         = os.environ.get("SKY_LLM_MODEL", DEFAULT_MODEL)
SUMMARY_MODEL = MODEL

# 记忆
MEMORY_UPDATE_MIN_PENDING = 6
# 记忆整合输出上限：整合器要原样保留【稳定】(最多1200字≈1600token) + 【最近】(200字)
# + 【日记】(100字)，520token 只够输出稳定段，导致最近段被截断、日记段永远不输出。
# 2200 能容纳完整三段；SKY_MEMORY_MAX_TOKENS 可调。
MEMORY_UPDATE_MAX_TOKENS = int(os.environ.get("SKY_MEMORY_MAX_TOKENS", "2200") or 2200)

# 搜索
SEARCH_ENABLED = os.environ.get("SKY_SEARCH_ENABLED", "1") != "0"
SEARCH_CACHE_SECONDS = 300
SEARCH_TIMEOUT = 4.5

# 风格学习
STYLE_LEARN_ENABLED = True

# 按键后端: "arduino"(硬件,推荐) / "pydirectinput" / "pyautogui" / "gamepad"
# 没有 Arduino 硬件时设为 "pydirectinput" 或 "pyautogui" 可测试聊天引擎逻辑
# 注意: 光遇PC版可能屏蔽软件模拟按键, 无硬件时按键可能不生效, 但OCR/LLM/记忆/搜索正常
BACKEND = os.environ.get("SKY_INPUT_BACKEND", "arduino")

# 使用者识别（白名单）: 开启后只回复白名单内玩家的消息, 路人消息直接丢弃
# SKY_WHITELIST_ENABLED=1 开启, =0 关闭（关闭后所有人都能触发, AI自己判断要不要回）
# SKY_WHITELIST=逗号分隔的玩家昵称, 如 "珂珂,秋,小明"
WHITELIST_ENABLED = os.environ.get("SKY_WHITELIST_ENABLED", "1") != "0"
WHITELIST = [n.strip() for n in os.environ.get("SKY_WHITELIST", "珂珂").split(",") if n.strip()]

WATCH_INTERVAL   = 0.10
# watch 真实节拍的打印间隔（秒）。真机反推只有 ~1 拍/秒，与设计值 10 拍/秒差一个量级，
# 而硬门防抖/邀请确认帧这些"按拍计数"的参数都按 0.4s/拍算，差多少必须看得见。
WATCH_CADENCE_LOG_EVERY = float(os.environ.get("SKY_WATCH_CADENCE_LOG_EVERY", "60.0"))
OCR_COOLDOWN     = 1.5
OCR_FALLBACK     = 10.0
# P0 插桩（2026-10-17）：只加日志、不改任何判定。用来分辨"背景动画噪声的帧差"和
# "真新消息到达的帧差"，并统计完整文本是否真的变了；采一轮真机日志后据此定双阈值。
# 采完可设 SKY_OCR_DIAG=0 关掉，无需改代码。
OCR_DIAG         = os.environ.get("SKY_OCR_DIAG", "1") != "0"
_ocr_diag_last_full = None   # 上一次 OCR 的完整文本（不截断），仅用于判断"全文是否变化"
CONFIRM_COOLDOWN = 6.0
REOPEN_COOLDOWN  = 6.0
PANEL_REOPEN_DELAY = 1.2  # 面板必须持续判关这么久才按C打开，滤掉瞬时漏检/抖动，避免把开着的面板按关
POST_ACTION_GRACE = 8.0
GOHOME_COOLDOWN  = 45.0

MAX_MSGS        = 40
SUMMARY_TRIGGER = 30
SUMMARY_KEEP    = 15

# ── 事件日记四层检索（记忆 v4）──
# ① 默认层：每次注入最近 DIARY_INJECT_RECENT 条日记（零检索成本）
# ② 触发层：消息命中回忆触发词 → 本地检索注入
# ③ 检索层：标签命中(权重5)+字面交集(权重1) 双通道打分（core/memory.diary_recall）
# ④ 协议层：AI 可输出 [RECALL]关键词[/RECALL] 自主发起检索 → 重答一次；
#    冷却 SKY_RECALL_COOLDOWN 秒内只许一次，重答里再出现 RECALL 一律剥离（防递归烧token）
DIARY_INJECT_RECENT = int(os.environ.get("SKY_DIARY_INJECT_RECENT", "3") or 3)
RECALL_COOLDOWN = float(os.environ.get("SKY_RECALL_COOLDOWN", "15") or 15)
RECALL_TRIGGER_WORDS = (
    "上次", "那天", "还记得", "第一次", "之前聊", "什么时候",
    "当初", "曾经", "回忆", "认识多久", "多久了",
)
_RECALL_TAG_RE = re.compile(r"\[RECALL\](.*?)\[/RECALL\]", re.S)
_recall_last_used = 0.0  # 上次 RECALL 时间戳（模块级，冷却控制）

# ── 分层记忆（记忆金字塔）：周记（周一切分）/月记/年记 ──
# 启动时自动补压缩已结束周期（SKY_SUMMARY_ON_STARTUP=0 可关，改用 summarize_memory.py）
SUMMARY_ON_STARTUP = os.environ.get("SKY_SUMMARY_ON_STARTUP", "1") != "0"
# 日常常驻注入最近一篇周记（"这周在干嘛"的连续感）；月/年记只在触发词命中时检索
WEEKLY_RECALL_WORDS = (
    "这周", "本周", "上周", "这一周", "上一周", "这周都",
)
MONTHLY_RECALL_WORDS = (
    "这个月", "上个月", "本月", "上月", "这一个月", "这阵子", "最近一阵",
)
YEARLY_RECALL_WORDS = (
    "今年", "去年", "这一年", "年度", "这一年来",
)
# 对话窗口持久化：重启接着聊（存最近 MAX_MSGS 条，原子写）
CONVERSATION_FILE = os.path.join(USER_DATA_DIR, "conversation.json")

DEBUG = True
# AI 对话转储开关：设 SKY_AI_TRACE=1 后，把每次发给LLM的完整输入/原始返回/最终处理
# 写到独立文件 logs/ai_trace_*.txt（只留最近5份），不刷主日志；默认关，零开销。
AI_TRACE_ENABLED = os.environ.get("SKY_AI_TRACE", "0") == "1"

# 云端视觉(VLM)低频顾问：SKY_VISION_ENABLED=1 启用，默认关、零开销。
# 用于姿势动作结果校验等本地视觉做不到的语义判断；失败/无key自动回退，不影响主循环。
# 模型用 SKY_VISION_MODEL 切换（默认 qwen-vl-plus）；key 走 SKY_VISION_API_KEY 或 vision_key.txt
VISION_ENABLED = os.environ.get("SKY_VISION_ENABLED", "0") == "1"
VISION_POSE_CHECK = os.environ.get("SKY_VISION_POSE", "1") != "0"
_vision = VisionClient(enabled=VISION_ENABLED)

AKI = "珂珂"              # 玩家大号名字（白名单默认包含，优先回复）
SELF_NAMES = ("星河",)     # AI操控的小号名字（自己的消息标记名，用于认领防自循环）

# YOLO 目标检测（可选，用于识别牵手图标等，比模板匹配识别率高）
# SKY_YOLO_ENABLED=1 开启, =0 关闭
# SKY_YOLO_MODEL=模型路径
# SKY_YOLO_CONF=置信度阈值
YOLO_ENABLED = os.environ.get("SKY_YOLO_ENABLED", "1") != "0"
# 默认模型：22 类 v6 版（0-11 同 v5：chat_input/hand_icon/open_tree_icon/accept_hug/
# accept_carry/accept_high_five/accept_fist_bump/follow/wing_light/light/focus/seat；
# 12-21 新增：bear_hug/curtain_call/fancy_high_five/handshake/head_pat/mutual_light/
# play_fight/shoulder/spin_dance/whisper），同学笔记本 N 卡 batch4 imgsz960 训练，
# 2026-10-21 验收（97 轮，best 第 72 轮），val：precision0.996/recall1.0/mAP50 0.995/
# mAP50-95 0.914，每类 recall 均为 1.0。旧 12 类模型保留在
# runs/detect/sky_v5_12cls/weights/best.pt，需要回退时设 SKY_YOLO_MODEL 指向它即可。
YOLO_MODEL_PATH = os.environ.get("SKY_YOLO_MODEL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "runs", "detect", "sky_v6_22cls", "weights", "best.pt"))
YOLO_CONF_THRESHOLD = float(os.environ.get("SKY_YOLO_CONF", "0.8"))
YOLO_DETECT_INTERVAL = 0.4  # YOLO检测间隔（秒），1秒约2.5次

# ── 好友互动邀请（对方发起 抱抱/背背/击掌/碰拳，屏幕出现圆形“接受”图标）──
# SKY_ACCEPT_AUTO=1 才真正自动按 F 接受；默认 0=只观测、打印 [互动邀请] 日志但不按 F。
# 先用观测模式在真机确认：邀请检出准、图标上方名字识别对、传送/加入等危险界面不会误触发，
# 再打开自动（见任务阶段2/3）。
ACCEPT_AUTO = os.environ.get("SKY_ACCEPT_AUTO", "0") == "1"
ACCEPT_CONF = float(os.environ.get("SKY_ACCEPT_CONF", "0.8"))  # 接受图标的判定阈值（实测最低0.86）
ACCEPT_CONFIRM_FRAMES = int(os.environ.get("SKY_ACCEPT_FRAMES", "2"))  # 连续几拍检测到才认定（防抖）
ACCEPT_COOLDOWN = float(os.environ.get("SKY_ACCEPT_COOLDOWN", "12.0"))  # 同一动作事件触发后冷却秒数
# 连续多少拍没检测到才认定图标真消失（消失宽限，防单帧漏检被当成新邀请）。约 0.4s/拍。
ACCEPT_MISS_FRAMES = int(os.environ.get("SKY_ACCEPT_MISS_FRAMES", "3"))
# 阶段3（B2）拟人执行参数：
# AI 同意后，台词发出到真正按 F 之间的自然延迟（让对方看到台词、像真人反应）。
ACCEPT_PRESS_DELAY = float(os.environ.get("SKY_ACCEPT_PRESS_DELAY", "2.0"))
# 兜底：邀请首次出现后，AI 一直没产出有效决策（verdict 仍 pending）超过该秒数，
# 且邀请安全（白名单/读不到名字），自动按 F 接受。AI 明确 decline 时不兜底。
ACCEPT_FALLBACK_SECONDS = float(os.environ.get("SKY_ACCEPT_FALLBACK", "15.0"))
# 模型 class id -> (内部动作名, 中文动作名)，顺序与 data.yaml/classes.txt 锁定一致，勿改
# 0-11 为 v5 原有；12-21 为 v6 新增的双人互动邀请（均按 F 接受）。
# 17 mutual_light（互火）是星盘鼠标点击、不是 F 邀请，故不纳入，只识别。
ACCEPT_CLASSES = {
    3: ("accept_hug", "抱抱"),
    4: ("accept_carry", "背背"),
    5: ("accept_high_five", "击掌"),
    6: ("accept_fist_bump", "碰拳"),
    12: ("bear_hug", "熊抱"),
    13: ("curtain_call", "谢幕"),
    14: ("fancy_high_five", "花式击掌"),
    15: ("handshake", "握手"),
    16: ("head_pat", "摸头"),
    18: ("play_fight", "打闹"),
    19: ("shoulder", "搭肩"),
    20: ("spin_dance", "共舞"),
    21: ("whisper", "耳语"),
}

# ── 拟人互动·阶段2（Delta B1）：邀请事件喂给 LLM，星河先开口反应（台词），
#    但【不按 F、不接受】，让互动有人味、不秒接；只发一句台词，零风险。
#    SKY_INTERACT_REACT=0 关闭（默认开）；只在白名单/默认接受的邀请触发（陌生人不理）。
#    离线测试（2026-10）确认：动作类型以 YOLO 为准（VLM 会把圆形图标都判成 hug，
#    不可信），VLM 只辅助看场景；阶段3（B2）才在反应后按 F + 15 秒兜底。
INTERACT_REACT_ENABLED = os.environ.get("SKY_INTERACT_REACT", "1") != "0"

# ── wing_light 光翼硬门（无翼小号保护，最高优先）：附近检出光翼时，在集中按键函数
#    _key 上拦截所有 F，绝不自动拾取（无翼捡光翼不可恢复）。只拦截、不新增自动按键。
#    SKY_WING_LIGHT_GUARD=0 关闭（默认开）；进入防抖，退出迟滞（见 watch_loop）。
WING_LIGHT_GUARD_ENABLED = os.environ.get("SKY_WING_LIGHT_GUARD", "1") != "0"
WING_LIGHT_CONF = float(os.environ.get("SKY_WING_LIGHT_CONF", "0.8"))  # 光翼检出阈值
WING_LIGHT_FRAMES = int(os.environ.get("SKY_WING_LIGHT_FRAMES", "2"))  # 连续几拍检出才进入防护（防抖）
WING_LIGHT_HOLD = float(os.environ.get("SKY_WING_LIGHT_HOLD", "2.0"))  # 最后检出后多少秒内继续拦截（退出迟滞）
# 新鲜度看护（2026-10-19 修 P0，见第六十三/六十四章）：正常运行时 seen=True 意味着 last_seen
# 至多 HOLD+检测间隔 秒前。若 seen 长时间为 True 却没再刷新，只可能是 wing_light_step 没被
# 调用（YOLO 失效/模型 None/取不到帧/检测异常被吞）或存在持续误检 —— 此时 F 会被永久静默锁死。
# 这里**不自动解锁**（安全优先，绝不冒险拾取光翼），只周期性大声告警让问题可见。
WING_LIGHT_STALE_WARN = float(os.environ.get("SKY_WING_LIGHT_STALE_WARN", "15.0"))  # 超过多少秒未刷新算可疑
WING_LIGHT_WARN_EVERY = float(os.environ.get("SKY_WING_LIGHT_WARN_EVERY", "10.0"))  # 告警最小间隔秒
# 逃生开关：确认是误检时才手动设（>0 生效，超过该秒数强制解除防护）。默认 0 = 永不自动解除。
WING_LIGHT_MAX_HOLD = float(os.environ.get("SKY_WING_LIGHT_MAX_HOLD", "0"))

# ── 光翼"UI 提示 F 安全窗"（2026-10-20 修 P0，见第七十章）────────────────────────
# 背景：open_tree_icon 与 wing_light 在同一枚圆形 F 图标上 elif 竞争分类，真机日志实证
# 两类会在相邻两拍摇摆（826 行 open_tree 0.91 / 827 行 [Guard] 进入防护）。硬门只在
# wing_light 那一拍胜出时才亮，open_tree 胜出的拍里"对着 UI 提示按 F"完全无保护。
# 对策：凡是对着屏幕上的圆形 F 提示按 F 的动作（好友树入口 / 接受邀请 / 牵手），
# 额外要求"最近 WING_LIGHT_F_SAFE_WINDOW 秒内从未检出过 wing_light"。
WING_LIGHT_F_SAFE_WINDOW = float(os.environ.get("SKY_WING_LIGHT_F_SAFE_WINDOW", "30.0"))
# 硬门未生效时是否仍允许这类 F（fail-safe 默认否：宁可不抱抱，也不冒拾光翼的风险）。
# 确认 YOLO 确实不可用且能人工保证安全时，才设 SKY_F_ALLOW_WITHOUT_GUARD=1 放行。
F_ALLOW_WITHOUT_GUARD = os.environ.get("SKY_F_ALLOW_WITHOUT_GUARD", "0") == "1"
# 光翼类在模型里的名字（23 类模型若改名，用本变量对齐，不要把名字写死在多处）
WING_LIGHT_CLASS_NAME = os.environ.get("SKY_WING_LIGHT_CLASS_NAME", "wing_light")

# YOLO 实时预览窗口：SKY_YOLO_PREVIEW=1 时弹出一个窗口，显示游戏画面叠加 YOLO 识别框
# （7 类 + 类别名 + 置信度），用于直观看“YOLO 此刻识别到了什么”。默认关，不影响运行。
# 窗口里按 q 或点右上角 × 只关闭预览，主程序继续跑。
YOLO_PREVIEW = os.environ.get("SKY_YOLO_PREVIEW", "0") == "1"
YOLO_PREVIEW_SCALE = float(os.environ.get("SKY_YOLO_PREVIEW_SCALE", "0.67"))  # 1920宽→约1280显示
_PREVIEW_WIN = "Sky YOLO Preview  (q=关闭预览)"

# 牵手图标上方名字 OCR 确认（避免误牵其他人）
# SKY_NAME_OCR_ENABLED=1 开启, =0 关闭（默认开启）
NAME_OCR_ENABLED = os.environ.get("SKY_NAME_OCR_ENABLED", "1") != "0"
NAME_OCR_ABOVE_PIXELS = 80   # 图标上方多少像素范围识别名字
NAME_OCR_SIDE_PIXELS = 60     # 图标左右各扩展多少像素
NAME_OCR_SCALE = 4             # 放大倍数（小字体需要放大）

# 模板匹配牵手检测开关：YOLO识别率更高，默认关闭模板匹配，只用YOLO
# SKY_TEMPLATE_HAND_ENABLED=1 开启, =0 关闭（默认关闭）
TEMPLATE_HAND_ENABLED = os.environ.get("SKY_TEMPLATE_HAND_ENABLED", "0") != "0"

# 内存读取聊天（可选，比OCR准确，但需要管理员权限，有封号风险）
# SKY_MEM_READER=1 启用, =0 关闭（默认关闭，使用OCR）
# 启用后跳过OCR聊天识别，直接从游戏内存读取聊天消息
MEM_READER_ENABLED = os.environ.get("SKY_MEM_READER", "0") == "1"

# 自动接传送/确认弹窗（好友传送过来时自动按空格接受）
# SKY_ACCEPT_TELEPORT=1 启用, =0 关闭（默认关闭：自动接弹窗会误触聊天面板，先手动接）
ACCEPT_TELEPORT_ENABLED = os.environ.get("SKY_ACCEPT_TELEPORT", "0") == "1"

# 全局 YOLO 模型和状态
_yolo_model = None
_yolo_last_detect_time = 0.0
_yolo_hand_detected = False  # 最近一次YOLO是否检测到牵手图标
_yolo_hand_conf = 0.0        # 最近一次检测的置信度
_yolo_hand_bbox = None       # 最近一次检测到的牵手图标位置 (x1, y1, x2, y2)
_yolo_name_ocr_result = None # 最近一次名字OCR识别结果
_yolo_chat_input_detected = False  # 最近一次YOLO是否检测到聊天输入框
_yolo_chat_input_conf = 0.0        # 最近一次检测的置信度
_yolo_chat_input_bbox = None       # 最近一次检测到的聊天输入框位置
_yolo_open_tree_detected = False   # 最近一次YOLO是否检测到好友树入口图标（按F打开好友树）
_yolo_open_tree_conf = 0.0         # 最近一次检测的置信度

# 好友互动邀请 accept_*（class 3-6）：最近一次检测结果 {action_name: (conf, bbox)}
_yolo_accept = {}
# 最近一次 YOLO 检测的全部框（供预览窗口画框）：[(cls, conf, (x1,y1,x2,y2), name)]，及检测时间
_yolo_preview_boxes = []
_yolo_preview_time = 0.0

# wing_light 光翼硬门状态（watch_loop 按 YOLO 检测更新，_key 读取）：
_yolo_wing_light_seen = False     # 防抖后的当前状态：附近有光翼（防护态）
_yolo_wing_light_frames = 0       # 连续检出计数（进入防抖）
_yolo_wing_light_last_seen = 0.0  # 最后一次检出时间（退出迟滞）
_yolo_wing_light_warn_at = 0.0    # 上次打印“防护态可疑”告警的时间（限流用）
# 光翼硬门是否真的在线（YOLO 已加载 且 模型里有 wing_light 类）。False 时按 fail-safe
# 处理：拒绝"对着 UI 提示按 F"的动作（见 f_prompt_press_allowed），并在启动日志大字告警。
_wing_light_guard_online = False
# 观测防抖/去重（仅 watch_loop 用）：连续拍数 / 上次事件时间 / 是否在屏 / 本轮首次出现时间
_accept_seen = {name: 0 for name, _ in ACCEPT_CLASSES.values()}
_accept_event_time = {name: None for name, _ in ACCEPT_CLASSES.values()}  # None=从未触发
_accept_present = {name: False for name, _ in ACCEPT_CLASSES.values()}
_accept_first_seen = {name: None for name, _ in ACCEPT_CLASSES.values()}
# 本次 present 周期是否已把邀请喂给 AI（事件级去重：一个持续图标只反应一次，图标消失才重置）
_accept_event_fed = {name: False for name, _ in ACCEPT_CLASSES.values()}
_accept_miss = {name: 0 for name, _ in ACCEPT_CLASSES.values()}  # 连续漏检拍数（消失宽限）
# 阶段3（B2）邀请决策状态：aname -> "pending"(图标在、等AI)/"accept"(AI同意,待按F)/
# "decline"(AI拒绝)/"done"(已按F接受)。None=当前无该邀请。图标消失时清回 None。
_accept_verdict = {name: None for name, _ in ACCEPT_CLASSES.values()}

# 确认对话框处理中标志（阻止重复触发确认）
_confirm_in_progress = False
_confirm_lock_time = 0.0

# 发送消息独占锁：发送期间（回车→粘贴→回车）阻止其他线程按键
_sending_msg = False

# 好友树动作执行中标志：期间 watch_loop 不抢按 F/ESC，避免误关正在导航的好友树
_friend_tree_busy = False
# 最近一次好友树动作结束时间：其后若干秒屏蔽"确认弹窗"自动空格——好友树界面/收起过渡
# 时 OCR 会把字误读成"前往"，触发通用确认逻辑乱按空格（真机 2026-09-09 21:52 实证）
_friend_tree_last_time = 0.0
FRIEND_TREE_CONFIRM_GUARD = 4.0

# 回遇境后15秒内不打开聊天框（避免干扰回遇境过程）
_last_gohome_time = 0.0
GOHOME_CHAT_COOLDOWN = 15.0  # 回遇境后多少秒内不打开聊天框

# ===================== System Prompt（人设） =====================

def _load_persona() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

SYSTEM = _load_persona()

# ===================== 停止信号 =====================

shutdown_event = threading.Event()

# ===================== MCP =====================

_rpc_counters = {}
_rpc_lock = threading.Lock()


def _next_rpc_id(name: str) -> int:
    with _rpc_lock:
        _rpc_counters[name] = _rpc_counters.get(name, 0) + 1
        return _rpc_counters[name]


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    if MCP_TOKEN:
        s.headers["Authorization"] = "Bearer " + MCP_TOKEN
    return s


def mcp(tool, args=None, session=None):
    sess = session or _make_session()
    payload = {
        "jsonrpc": "2.0", "id": _next_rpc_id(tool),
        "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}}
    }
    for attempt in range(3):
        try:
            r = sess.post(MCP_URL, json=payload, timeout=30)
            d = r.json()
            if "error" in d:
                raise RuntimeError("MCP: " + str(d["error"]))
            res = d.get("result", {})
            text = "\n".join(
                i["text"] for i in res.get("content", [])
                if isinstance(i, dict) and i.get("type") == "text"
            )
            if res.get("isError"):
                raise RuntimeError(f"MCP拒绝({tool}): {text}")
            return text
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            if attempt < 2:
                print(f"  [MCP] 超时，重试 ({attempt+2}/3)...")
                time.sleep(1)
            else:
                raise RuntimeError(f"MCP连续3次超时: {e}")

# ===================== SharedState（对话 + 记忆 + 搜索 + 风格） =====================

def _apply_recall_protocol(reply, work_messages, state, llm_client, player_msg=""):
    """记忆 v4 ④：处理 AI 输出的 [RECALL]关键词[/RECALL] 协议（AI 自主检索日记）。

    规则（防失控三道闸）：
      - 冷却：SKY_RECALL_COOLDOWN 秒内只处理一次，期内直接剥离标签用原回复（不调 LLM）
      - 深度：无论检索有没有结果，最多重新生成 1 次；重答结果无条件剥离标签（防递归烧token）
      - 空手：检索无结果时如实告知模型，让它诚实说"记不清"，而不是继续乱猜
    任何路径返回前都会剥离 [RECALL] 标签 —— 绝不让暗号出现在游戏输入框。

    返回 (final_reply, used_recall: bool)
    """
    reply = reply or ""
    m = _RECALL_TAG_RE.search(reply)
    if not m:
        # 保险：普通回复里万一残留标签也剥掉
        return _RECALL_TAG_RE.sub("", reply).strip(), False
    now = time.time()
    if now - getattr(state, "recall_last_used", 0.0) < RECALL_COOLDOWN:
        return _RECALL_TAG_RE.sub("", reply).strip(), False
    state.recall_last_used = now
    kw = (m.group(1) or "").strip() or str(player_msg or "")
    # 同时检索事件日记（diary_recall）与分层总结（周/月/年记，summary_recall）
    hits = diary_recall(kw)
    sum_hits = summary_recall(kw, levels=("weekly", "monthly", "yearly"))
    parts = []
    if hits:
        parts.append("【事件日记】\n"
                     + "\n".join(f"- {i['time']} {i['event']}" for _, i in hits))
    if sum_hits:
        _lv_name = {"weekly": "周记", "monthly": "月记", "yearly": "年记"}
        parts.append("【周记/月记/年记】\n"
                     + "\n".join(f"- [{_lv_name.get(lv, lv)}] {it.get('period','')}："
                                 f"{it.get('text','')}" for _, lv, it in sum_hits))
    if parts:
        feed = ("（系统已帮你检索记忆，关键词「%s」，找到：\n%s\n"
                "请基于以上记忆自然回答珂珂，不要再输出 [RECALL] 标签）"
                % (kw, "\n".join(parts)))
    else:
        feed = ("（系统已帮你检索记忆（事件日记和周/月/年记都查过），关键词「%s」，"
                "但没有找到相关记录。请诚实自然地回应，可以说自己记不清了，"
                "不要再输出 [RECALL] 标签）" % kw)
    # 标准 tool 模式：把带 RECALL 的原回复 + 检索结果一起给模型，让它接着答
    work_messages.append({"role": "assistant", "content": reply})
    work_messages.append({"role": "user", "content": feed})
    print(f"  [Diary] AI 自主RECALL「{kw}」→ 事件{len(hits)}条 "
          f"分层{len(sum_hits)}篇，重新作答")
    try:
        msg2 = llm_client.chat_raw(work_messages, temperature=0.7, max_tokens=300)
        final = msg2.get("content") or reply
    except Exception as e:
        print(f"  [Diary] RECALL 重答失败，回退原回复: {e}")
        final = reply
    return _RECALL_TAG_RE.sub("", final).strip(), True


def _load_conversation() -> list:
    """启动时加载持久化的对话窗口（重启接着聊）。无文件/损坏时空列表启动。"""
    try:
        data = _load_json(CONVERSATION_FILE)
    except Exception:
        data = None
    if not isinstance(data, list):
        return []
    conv = [
        m for m in data
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    return conv[-MAX_MSGS:]


def _save_conversation(conv) -> None:
    """把对话窗口原子写到磁盘（最近 MAX_MSGS 条；失败只打日志，绝不让主循环崩）。"""
    try:
        _atomic_write_json(CONVERSATION_FILE, list(conv)[-MAX_MSGS:])
    except Exception as e:
        print(f"  [Conv] 对话持久化失败: {e}")


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        # 对话窗口持久化（记忆 v4）：重启后从磁盘加载，能接着上次的话题聊
        self.conversation: list[dict] = _load_conversation()
        # 记忆 v4：RECALL 协议冷却时间戳
        self.recall_last_used: float = 0.0
        self.recent_seen: list[tuple[str, float]] = []
        self.sent_history: list[tuple[str, float]] = []
        self.action_busy: bool = False
        self.last_confirm_time: float = 0
        self.last_chat_ocr_time: float = 0
        self.last_reopen_time: float = 0
        self.last_action_end: float = 0
        self.last_fhand_time: float = 0
        self.last_fhand_busy_log: float = 0
        self.last_gohome_time: float = 0
        self.last_open_panel_fail: float = 0  # 打开面板失败时间，用于冷却

        # ── 牵手状态（逻辑推断）──
        self.is_holding_hands: bool = False   # 当前是否在牵手状态
        self.hold_hands_start_time: float = 0  # 牵手开始时间
        self.hold_hands_timeout: float = 180.0  # 牵手超时重置（秒），防止状态卡住

        # ── 姿势状态（逻辑推断）──
        self.pose_state: str = "standing"  # standing/crouching/sitting/lying
        self.pose_update_time: float = 0
        self.pose_need_retry: bool = False  # 需要重试姿势动作（玩家说没成功时）
        self.pose_last_target: str = ""  # 最近一次姿势目标（玩家反馈时据此判断补按）

        # ── 回遇境完成标志（让AI知道刚刚回到遇境）──
        self.gohome_completed: bool = False  # 回遇境成功后设为True，AI回复后清除

        # ── 移植自 sky-companion ──
        self.memory = load_memory()
        self.search_knowledge = load_search_knowledge()
        self.style_knowledge = load_style_knowledge()
        self.style_checked = False
        self.my_words: list[str] = []          # 最近AI回复（最多8条，防重复）
        self.memory_updating: bool = False
        self.search_cache: dict = {}            # {query_key: (timestamp, context)}

        # ── 待处理互动邀请（图标持续在屏期间保留；AI 据此知道邀请是否还在等待）──
        # {aname: {"action_cn": 中文, "first_seen": 时间}}；watch 线程写、ai_loop 读，均过 state.lock
        self.pending_invites: dict = {}

# ===================== 面板操作（读 WorldState） =====================

def panel_open(det: PanelDetector) -> bool | None:
    """判断聊天面板是否打开。三态主裁（2026-09-10 接入，159 帧真机标定）：
    1. hint_bar 底部提示行模板相关分明确时它说了算（开>=0.50 / 关<=0.20）——
       "面板开独有、关必无、位置固定"的锚点，治传统暗横条在关帧咬死 1.00 的死锁
       （续26 实机定性、续42 离线全量验证：开 0.55~0.84 / 关 0~0.44，空档清晰）；
    2. hint 分落在中间带（关闭动画渐隐/亮景火光/没放模板/本帧没算分）时，
       回退 fuse_panel_fallback：YOLO 可用就以 YOLO 为准（不再 OR 传统通道，
       否则暗场景里传统暗横条会咬死 1.00 把面板判成一直开着，守门于是从不按 C）。
       回退策略可用 SKY_PANEL_FALLBACK=legacy 切回旧的 OR 语义。
    """
    snap = det.world.snapshot()
    if snap.screen == Screen.LOADING:
        return None
    hint = snap.metrics.get("hint_bar")
    state = judge_panel_by_hint(hint)
    if state == "open":
        return True
    if state == "closed":
        return False
    # hint 中间带/无模板 -> 本帧看不清，走 fuse_panel_fallback：
    # 默认 YOLO 可用时**只信 YOLO**。旧写法是 "YOLO OR 传统"，会被暗场景里咬死 1.00 的
    # 传统暗横条通道带偏 —— 2026-10-06 真机事故：面板实际没开，hint 全程 0.26~0.42 落中间带，
    # 回退后传统通道给 chat=开(1.00)，于是本函数一直返回 True，守门从不按 C，
    # 用户看到的就是"按键没触发、聊天面板打不开"。
    traditional = snap.chat_open.value
    yolo_ok = bool(YOLO_ENABLED and _yolo_model is not None)
    return fuse_panel_fallback(yolo_ok, _yolo_chat_input_detected, traditional)


def wait_panel(det, want: bool, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if panel_open(det) is want:
            return True
        time.sleep(0.05)
    return False


def _yolo_says_closed() -> bool | None:
    """YOLO优先的面板状态判断。YOLO可用时以YOLO为准，返回None表示YOLO不可用。"""
    if YOLO_ENABLED and _yolo_model is not None:
        return not _yolo_chat_input_detected
    return None


def _wait_panel_yolo(det, want_closed: bool, timeout: float) -> bool:
    """等待面板状态，YOLO可用时以YOLO为准。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        yolo_closed = _yolo_says_closed()
        if yolo_closed is not None:
            if yolo_closed == want_closed:
                return True
        else:
            if panel_open(det) is (not want_closed):
                return not want_closed
            if panel_open(det) is want_closed:
                return want_closed
        time.sleep(0.1)
    return False


def wing_light_step(found, now, seen, frames, last_seen,
                    frames_needed=WING_LIGHT_FRAMES, hold=WING_LIGHT_HOLD):
    """纯函数（无副作用）：光翼硬门状态机推进一拍。
    入参：本拍是否检出 wing_light、当前时刻、上一拍状态（seen/frames/last_seen）。
    返回新状态 (seen, frames, last_seen)：
      - 检出：连续帧 +1，达到 frames_needed 才进入防护（防抖），并刷新 last_seen；
      - 未检出：帧计数清零；已在防护且距 last_seen 超过 hold 才解除（退出迟滞）。
    供 watch_loop 调用，离线可单测。"""
    if found:
        frames = frames + 1
        if frames >= frames_needed:
            return True, frames, now
        return seen, frames, last_seen
    frames = 0
    if seen and last_seen and now - last_seen > hold:
        return False, frames, last_seen
    return seen, frames, last_seen


def f_prompt_press_allowed(now=None, last_seen=None, guard_online=None,
                           window=None, allow_without_guard=None,
                           guard_enabled=None):
    """纯决策（离线可单测）：此刻能否对着**屏幕上的圆形 F 提示**按 F。

    返回 (allowed: bool, reason: str)。allowed=False 时 reason 是被拒原因（直接打日志）。

    为什么需要它（2026-10-20 排查出的 P0，见第七十章）：`_key()` 里的硬门只在
    `wing_light` 这一拍的分类竞争中胜出时才亮；而 `open_tree_icon` 与 `wing_light`
    在同一枚图标上是 `elif` 互斥的，真机日志实证会摇摆。于是"为了开好友树/接受邀请
    而按 F"这条路径在 open_tree 胜出的拍里是裸奔的。这里给它单独加一道**时间窗**：
    只要最近 window 秒内见过 wing_light，就一律不按（光翼附近本来也不该按 F）。
    """
    if guard_enabled is None:
        guard_enabled = WING_LIGHT_GUARD_ENABLED
    if not guard_enabled:
        return True, "硬门已被手动关闭（SKY_WING_LIGHT_GUARD=0）"
    if guard_online is None:
        guard_online = _wing_light_guard_online
    if allow_without_guard is None:
        allow_without_guard = F_ALLOW_WITHOUT_GUARD
    if not guard_online and not allow_without_guard:
        return False, ("光翼硬门未生效（YOLO 不可用或模型无 wing_light 类），"
                       "拒绝按 F；确认安全可设 SKY_F_ALLOW_WITHOUT_GUARD=1")
    if window is None:
        window = WING_LIGHT_F_SAFE_WINDOW
    now = time.time() if now is None else now
    last_seen = _yolo_wing_light_last_seen if last_seen is None else last_seen
    if last_seen and (now - last_seen) <= window:
        return False, (f"最近 {now - last_seen:.1f}s 内检出过光翼"
                       f"（安全窗 {window:.0f}s 内禁止对着 UI 提示按 F）")
    return True, ""


def _wing_light_near(now=None):
    """是否处于光翼附近（防护态）：防抖状态 + last_seen 退出迟滞兜底。
    供 _key 拦截 F；WING_LIGHT_GUARD_ENABLED 关闭时恒 False。安全优先（宁可多拦）。

    2026-10-19 加新鲜度看护（修第六十三章 P0）：seen 是“粘住”的标志，只有 wing_light_step
    能解除它。若 step 不再被调用（YOLO 失效/模型 None/取不到帧/检测异常被吞）或存在持续误检，
    seen 会永远为 True，导致所有 F 被静默永久拦截。这里检测到“seen 长时间未刷新”就周期性
    大声告警（默认不自动解锁，安全优先）；确认真是误检时可设 SKY_WING_LIGHT_MAX_HOLD 自动解除。
    """
    global _yolo_wing_light_seen, _yolo_wing_light_warn_at
    if not WING_LIGHT_GUARD_ENABLED:
        return False
    now = now or time.time()
    if _yolo_wing_light_seen:
        age = (now - _yolo_wing_light_last_seen) if _yolo_wing_light_last_seen else 0.0
        # 逃生开关（默认关）：确认误检时手动开启，超时强制解除
        if WING_LIGHT_MAX_HOLD > 0 and age > WING_LIGHT_MAX_HOLD:
            _yolo_wing_light_seen = False
            print(f"  [Guard] 防护已持续 {age:.0f}s 超过 SKY_WING_LIGHT_MAX_HOLD="
                  f"{WING_LIGHT_MAX_HOLD:.0f}s，强制解除（用户确认误检时的逃生开关）")
            return False
        if age > WING_LIGHT_STALE_WARN and now - _yolo_wing_light_warn_at >= WING_LIGHT_WARN_EVERY:
            _yolo_wing_light_warn_at = now
            print(f"  [Guard] ⚠ 警告：防护态已持续 {age:.0f}s 未再刷新，F 仍在拦截中。"
                  f"通常是 YOLO 未再推进状态机（模型失效/取不到帧/检测异常）或 wing_light 持续误检。"
                  f"若确认误检：设 SKY_WING_LIGHT_GUARD=0 关硬门，或 SKY_WING_LIGHT_MAX_HOLD=秒数 自动解除。")
        return True
    return bool(_yolo_wing_light_last_seen) and now - _yolo_wing_light_last_seen <= WING_LIGHT_HOLD


def _key(sess, k, ms=80):
    if str(k).lower() == "f" and _wing_light_near():
        print("  [Guard] 附近有光翼，拦截 F（无翼保护，不拾取光翼）")
        return
    mcp("press_key", {"key": k, "duration_ms": ms, "backend": BACKEND},
        session=sess)


def open_panel_plan(obs, need=2, cooldown=6, max_press=2):
    """纯决策（离线可单测），与 close_panel_plan 镜像：obs 是逐拍‘面板是否已开’序列。
    连续 need 拍开 -> done；连续 need 拍【明确关】、且距上次按C超 cooldown 拍、未超
    max_press -> press。第一次按不受 cooldown 限制（进入时确认为关即按），之后补按必须
    等 cooldown 拍（打开动画+检测稳定），杜绝连按把面板 toggle 来 toggle 去。"""
    acts = []
    cstreak = ostreak = 0
    presses = 0
    ever_opened = False  # 是否曾见过“开”：见过就不再补按（面板不会自己关，之后判关是漏检）
    last_press = -10**9
    for i, opened in enumerate(obs):
        if opened:
            cstreak += 1
            ostreak = 0
            ever_opened = True
            if cstreak >= need:
                acts.append("done")
                return acts
        else:
            ostreak += 1
            cstreak = 0
            if (ostreak >= need and presses < max_press and not ever_opened
                    and i - last_press > cooldown):
                acts.append("press")
                presses += 1
                last_press = i
                ostreak = 0
                continue
        acts.append("wait")
    return acts


def _ensure_panel_open_for_watch(det, sess, timeout=8.0, need=2, gap=0.15,
                                 press_cooldown=5.0, max_press=2):
    """看门打开聊天面板：连续 need 次确认开才算成功。补按前提更严格——必须从未见过‘开’
    且连续 need 次明确关、距上次按C超 press_cooldown 才补下一次。一旦见过开，说明面板已
    打开，之后判关是检测漏检，绝不再按C（再按会把开着的面板关掉=肉眼看到的一闪）。
    press_cooldown=5.0（2026-10-22 由1.8上调）：YOLO 识别聊天框较慢，1.8s 时面板刚打开
    还没识别上，补按第二次C会把它又关掉；给足5秒、确认真没打开再补。timeout 同步8s。"""
    start = time.time()
    cstreak = ostreak = 0
    presses = 0
    ever_opened = False
    last_press = -999.0
    while time.time() - start < timeout and not shutdown_event.is_set():
        opened = panel_open(det) is True
        now = time.time()
        if opened:
            cstreak += 1
            ostreak = 0
            ever_opened = True
            if cstreak >= need:
                if presses:
                    print(f"  [Action] 面板已稳定打开（连续{need}次确认，共按C {presses} 次）")
                return True
        else:
            ostreak += 1
            cstreak = 0
            if (ostreak >= need and presses < max_press and not ever_opened
                    and now - last_press > press_cooldown):
                print(f"  [Action] 按C打开面板（第{presses+1}次）")
                _key(sess, "c", 220)  # 150ms 在游戏掉帧时偶发丢键，加长到220ms
                presses += 1
                last_press = now
                ostreak = 0
        time.sleep(gap)
    print(f"  [Action] {timeout:.0f}s 内未能确认面板打开（共按C {presses} 次）")
    return False


def _open_panel(det, sess, state) -> bool:
    snap = det.world.snapshot()
    # 加载/过场/传送确认弹窗/非游戏内：按C无意义还会捣乱，一律不动面板
    # HOME（遇境）也有聊天面板，守门需要能在遇境重开（回遇境/牵手后）
    if snap.screen not in (Screen.IN_WORLD, Screen.HOME) or snap.confirm_dialog.value:
        return False
    # 失败退避（修复：旧代码记录的是进入函数时的时间戳，内部循环十几秒后已过期，
    # 导致冷却形同虚设、不停重试按C；这里失败时在返回前取当前时间，见下方）
    if time.time() - state.last_open_panel_fail < 8.0:
        return False
    ok = _ensure_panel_open_for_watch(det, sess)
    if not ok:
        state.last_open_panel_fail = time.time()  # 必须用当前时间，冷却才真正生效
        print("  [Action] 打不开面板，冷却8秒")
    return ok


def _reopen_panel_after_action(det, sess, state):
    """动作（姿势/抱抱）后主动重开聊天面板，不等守门 1.2s，缩短"消息读不到"的窗口。
    真机根因（2026-10-06 复核）：姿势动作主动关面板、抱抱按F时游戏自动关聊天框，
    动作结束后代码原本不重开，只等守门，期间玩家说话无人应（"发呆"）。
    安全：_open_panel 内有 ever_opened 防抖——面板若仍开着不会按 C；回遇境冷却/
    弹窗/好友树仍在时不动。设 SKY_REOPEN_AFTER_ACTION=0 可整体关闭。"""
    if not REOPEN_PANEL_AFTER_ACTION:
        return
    snap = det.world.snapshot()
    if snap.screen not in (Screen.IN_WORLD, Screen.HOME):
        return
    if snap.confirm_dialog.value or snap.friend_tree.value:
        return
    if time.time() - _last_gohome_time < GOHOME_CHAT_COOLDOWN:
        return
    if panel_open(det) is not True:
        print("  [Action] 动作后主动重开聊天面板")
        _open_panel(det, sess, state)


def _close_panel(det, sess) -> bool:
    # 多次检测确认面板状态，避免误判导致反复按C
    def _check_closed():
        for _ in range(5):
            if panel_open(det) is not True:
                return True
            time.sleep(0.15)
        return False
    if _check_closed():
        return True
    # 只按C关闭面板（不按Enter，Enter会激活未激活的输入框导致C被输入成字母）
    _key(sess, "c", 150)
    if wait_panel(det, False, 2.0):
        return True
    # 按C后如果面板还开着，再按一次
    if panel_open(det):
        time.sleep(0.5)
        _key(sess, "c", 150)
        if wait_panel(det, False, 2.0):
            return True
    return _check_closed()


def _verify_pose_with_vision(det, sess, target_pose, max_round=2, settle=1.2):
    """按完姿势键后用【完整帧 VLM】看一眼实际姿势。
    核心防误补按：每次都观察两次（中间隔 0.4s），只有两次都 sure 且结果一致，
    才采信这个姿势——单次误判/认错人/动画中间帧都不会触发补按。
      · 两次一致且到目标 -> 成功；
      · 两次不一致或有 sure=false -> 看不清，回退盲信目标，绝不乱补；
      · 两次一致但都没到目标 -> 补按1次3，再看；补按后距离没变小就停，防绕圈站起。"""
    def _observe(wait):
        if wait:
            time.sleep(wait)  # 等姿势动画落定，避免看到中间帧
        frame = det.latest_frame()
        pose, sure = _vision.classify_pose(frame)  # 完整帧，能看到头顶名字区分自己/别人
        return pose if sure else None

    p1 = _observe(settle)
    p2 = _observe(0.4)
    print(f"  [Vision] 姿势校验: 目标={target_pose} 观察=({p1},{p2})")
    if p1 == target_pose and p2 == target_pose:
        return target_pose
    if p1 is None or p2 is None or p1 != p2:
        print("  [Vision] 两次观察不一致/不确定，回退盲信目标")
        return target_pose

    observed = p1
    for _ in range(max_round):
        if observed not in POSE_STATES:
            break
        dist = pose_press_delta(POSE_STATES[observed], POSE_STATES[target_pose])
        if dist == 0:
            break
        print(f"  [Vision] 两次确认{observed}未到{target_pose}（差{dist}），补按1次3")
        _key(sess, "3", 80)
        time.sleep(0.55)
        n1 = _observe(settle)
        n2 = _observe(0.4)
        if n1 is None or n2 is None or n1 != n2:
            print("  [Vision] 补按后观察不一致，停止并回退盲信")
            return target_pose
        new_dist = pose_press_delta(POSE_STATES[n1], POSE_STATES[target_pose])
        if n1 == target_pose:
            return target_pose
        if new_dist >= dist:
            print("  [Vision] 补按后未更接近目标，停止补按以防绕圈站起")
            return target_pose
        observed = n1
    return target_pose


def _select_closed(use_yolo, yolo_closed, panel_val):
    """纯决策：这一帧面板是否已关。panel_val 来自 panel_open()=YOLO OR 传统，只有它
    is not True（YOLO 无检测【且】传统也判关）才算关——双通道确认。
    历史教训双向：早期 OR 被传统关闭后的滞后误报带偏（靠调用方连续帧+冷静期消化）；
    后来改成‘只信YOLO’，结果 YOLO 漏检（面板开着却无检测）时误判已关、在开着的历史
    面板上直接 send_chat，enter 把输入框卡在激活态，之后 C 被当打字吞、怎么都关不掉
    （2026-09-04 实机‘打开2秒就关/关不掉’根因）。故判关必须两通道都不认为开。"""
    return panel_val is not True


def _panel_closed_now(det):
    """单帧面板是否已关（YOLO 优先，传统兜底）。"""
    use_yolo = YOLO_ENABLED and _yolo_model is not None
    yc = _yolo_says_closed() if use_yolo else None
    return _select_closed(use_yolo, yc, panel_open(det))


def _closed_streak_ok(closed_flags, need=2):
    """纯状态机：closed_flags 是一串‘是否已关’布尔，连续 need 个 True 才算稳定关。"""
    streak = 0
    for f in closed_flags:
        if f is True:
            streak += 1
            if streak >= need:
                return True
        else:
            streak = 0
    return False


def _stable_closed_check(results, need=2):
    """纯状态机：results 是一串 panel_open 判定（True=开，False/None=非开）。
    必须【连续 need 次非开】才算稳定关闭，中间出现一次开就重新计数——扛面板
    关闭动画与单帧抖动，避免‘一帧判关就放行、回车按在还开着的面板上’。"""
    streak = 0
    for r in results:
        if r is not True:
            streak += 1
            if streak >= need:
                return True
        else:
            streak = 0
    return False


def _wait_panel_stable_closed(det, timeout=2.0, need=2, gap=0.15):
    """在 timeout 内轮询，直到连续 need 次判定‘已关’（YOLO 优先），否则 False。"""
    deadline = time.time() + timeout
    seq = []
    while time.time() < deadline:
        seq.append(_panel_closed_now(det))
        if _closed_streak_ok(seq, need):
            return True
        time.sleep(gap)
    return False


def close_panel_plan(obs, need=2, cooldown=6, max_press=3, initially_open=True):
    """纯决策（离线可单测）：obs 是逐拍‘面板是否已关’的布尔序列，返回每拍动作
    'wait'/'press'/'done'。连续 need 拍关 -> done；连续 need 拍【明确开】、且距上次
    按C超过 cooldown 拍、补按未超 max_press -> press。按C后先等 cooldown 拍（关闭动画），
    期间不补按，杜绝把正在关的面板又按开（toggle 重开，实机‘不回消息’根因）。"""
    acts = []
    cstreak = ostreak = 0
    presses = 1 if initially_open else 0
    ever_closed = False  # 曾见过“关”就不再补按（之后判开是YOLO滞后误报，再按会重开）
    last_press = -1 if initially_open else -10**9
    for i, closed in enumerate(obs):
        if closed:
            cstreak += 1
            ostreak = 0
            ever_closed = True
            if cstreak >= need:
                acts.append("done")
                return acts
        else:
            ostreak += 1
            cstreak = 0
            if (ostreak >= need and presses < max_press and not ever_closed
                    and i - last_press > cooldown):
                acts.append("press")
                presses += 1
                last_press = i
                ostreak = 0
                continue
        acts.append("wait")
    return acts


# ===== 底部按键提示行主裁（续28 设计 -> 续42 159帧1080p标定 -> 续43 接入主循环）=====
# 2026-09-10 全量实测（159 帧、4 张多场景模板、真实 TemplateBank 路径）：
# 真开 0.55~0.84（主流 0.82+，输入框激活态最低 0.474）、真关 0.00~0.44
# （亮景/火光关帧偏高，中间带很宽），空档清晰。阈值取在空档中间留余量。
# 中间带一律不表态、回退旧双通道融合，绝不带着旧暗横条在中间带翻转。
HINT_BAR_OPEN_MIN = 0.50     # 相关分>=此值 -> 面板开（底部 ESC退后/ENTER聊天/T语音输入 在位）
HINT_BAR_CLOSED_MAX = 0.20   # 相关分<=此值 -> 面板关（纯游戏画面，无提示行）
# 二者之间 (0.20, 0.50) = 看不清 / 关闭动画渐隐 / 亮景火光干扰 -> 不表态、回退旧判据

# hint 落中间带时的回退策略（2026-10-06 加）：
#   yolo（默认）—— YOLO 可用时**只信 YOLO**；
#   legacy      —— 旧的 "YOLO OR 传统"，任一认为开就判开。
# 为什么改：真机实测（20:13 那次）面板**实际没开**，但 hint 全程落在中间带
# （0.26~0.42/unknown），回退到旧融合后，传统暗横条通道给出 chat=开(visual 1.00)，
# 于是 panel_open() 一直返回 True -> 守门以为面板开着、**从不按 C** ->
# 用户看到的就是"按键没触发、聊天面板打不开"。
# 传统通道在暗场景咬死 1.00 是**文档里记过的既有问题**（见 panel_open 的 docstring），
# hint_bar 当初就是为治它才引入的；既然主裁这帧看不清，就不该让那个已知会误报的通道
# 单独定论。而 YOLO 是专门训练来认聊天输入框的，且面板真开时它稳定给 0.96~0.97
# （19:56 那次真机日志），所以"YOLO 在场就以它为准"既治误报、也不引入新的翻转风险。
# 回退开关：set SKY_PANEL_FALLBACK=legacy
PANEL_FALLBACK = os.environ.get("SKY_PANEL_FALLBACK", "yolo").strip().lower() or "yolo"
PANEL_FALLBACK_YOLO_ONLY = PANEL_FALLBACK != "legacy"


def judge_panel_by_hint(hint_score, open_min=HINT_BAR_OPEN_MIN,
                        closed_max=HINT_BAR_CLOSED_MAX):
    """纯决策：底部提示行模板相关分 -> 'open'/'closed'/'unknown' 三态。
    None（未放模板 / 本帧没算分）一律 unknown。"""
    if hint_score is None:
        return "unknown"
    if hint_score >= open_min:
        return "open"
    if hint_score <= closed_max:
        return "closed"
    return "unknown"


def fuse_panel_fallback(yolo_available, yolo_detected, traditional,
                        yolo_only=None):
    """纯决策：hint 落中间带时的回退融合，返回 True(开)/False(关)。

    - YOLO 可用且 yolo_only：**只信 YOLO**（默认，治暗场景传统通道咬死 1.00 的误报）
    - YOLO 可用且非 yolo_only：旧的 OR 语义（YOLO OR 传统，任一认为开就判开）
    - YOLO 不可用：只能退回传统通道（此时没有更好的信息）
    yolo_only 默认取 PANEL_FALLBACK_YOLO_ONLY，测试里可显式传参。
    """
    if yolo_only is None:
        yolo_only = PANEL_FALLBACK_YOLO_ONLY
    if not yolo_available:
        return bool(traditional)
    if yolo_only:
        return bool(yolo_detected)
    return bool(yolo_detected or traditional)


def fuse_panel_verdict(hint_score, prev_open,
                       open_min=HINT_BAR_OPEN_MIN, closed_max=HINT_BAR_CLOSED_MAX):
    """纯决策（未来的面板主裁，离线可单测）：提示行明确时它说了算。
    返回 (verdict, source)：verdict=True 开 / False 关 / None 保持上一稳定态；
    source='hint' 为提示行明确裁决，'hold' 为本帧看不清、沿用 prev_open。
    治死锁的关键：中间带绝不被旧暗横条（面板关时它也可能咬死 1.00）带着翻转。"""
    state = judge_panel_by_hint(hint_score, open_min, closed_max)
    if state == "open":
        return True, "hint"
    if state == "closed":
        return False, "hint"
    return prev_open, "hold"


# ===== 临时调试：关面板过程自动存帧，用于离线标定聊天面板锚点（定位后可整体删除）=====
PANEL_CLOSE_DEBUG = True              # 总开关，改 False 即关闭自动存帧
PANEL_CLOSE_DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_close_debug")  # 绝对路径：任意工作目录启动都落到本项目根
PANEL_CLOSE_DEBUG_KEEP = 5            # 只保留最近 5 组，避免堆积
_PANEL_CLOSE_TICKS = (0.25, 0.55, 0.9, 1.3, 1.8, 2.4, 3.1, 4.0, 5.0)  # 进入后相对秒点


def _panel_debug_new_group():
    """为本次关面板建一个采样目录，并只保留最近 KEEP 组；任何失败返回 None（不影响主流程）。"""
    try:
        base = PANEL_CLOSE_DEBUG_DIR
        os.makedirs(base, exist_ok=True)
        groups = sorted(
            d for d in os.listdir(base)
            if d.startswith("close_") and os.path.isdir(os.path.join(base, d)))
        for old in groups[:-(PANEL_CLOSE_DEBUG_KEEP - 1)] if len(groups) >= PANEL_CLOSE_DEBUG_KEEP else []:
            shutil.rmtree(os.path.join(base, old), ignore_errors=True)
        g = os.path.join(base, "close_" + time.strftime("%H%M%S"))
        os.makedirs(g, exist_ok=True)
        return g
    except Exception:
        return None


def _panel_debug_save(det, folder, tag, t0):
    """存当前原始帧(BGR->png)，文件名记录相对秒、chat判定/置信/来源、YOLO、关键视觉子分值。"""
    if not folder:
        return
    try:
        fr = det.latest_frame()
        if fr is None:
            return
        c = det.world.snapshot().chat_open
        mt = det.world.snapshot().metrics or {}
        el = time.time() - t0
        name = (f"{tag}_t{el:04.1f}_chat{int(bool(c.value))}_{c.confidence:.2f}_{c.source}"
                f"_y{int(_yolo_chat_input_detected)}{_yolo_chat_input_conf:.2f}"
                f"_band{(mt.get('chat_band') or -1):.2f}_wod{(mt.get('chat_wod') or -1):.3f}"
                f"_tmpl{(mt.get('tmpl_chat') or -1):.2f}.png")
        if not cv2.imwrite(os.path.join(folder, name), fr):
            print("  [Action] 关面板采样帧写入失败(imwrite返回False)")
    except Exception:
        pass


def _ensure_panel_closed_for_send(det, sess, timeout=6.0, need=2, gap=0.15,
                                  press_cooldown=1.4, max_press=3):
    """发送前把聊天历史面板确实关掉：连续 need 次确认关才返回 True。补按前提=从未见过‘关’
    且连续 need 次仍明确开、距上次按C超 press_cooldown；见过关后再判开是检测滞后误报，
    不再补按（再按会把已关面板重新打开）。
    注意：关闭一律只用 C，【绝不能用 ESC】——面板一旦已关、游戏处于主界面时按 ESC 会呼出
    设置主菜单（2026-09-04 实机踩坑）。早先担心的‘输入框激活吞C’其源头是误判已关后在开着
    的面板走回车，已由 _select_closed 双通道判关从源头消除，无需 ESC 兜底。"""
    start = time.time()
    _dbg = _panel_debug_new_group() if PANEL_CLOSE_DEBUG else None
    _dbg_ticks = list(_PANEL_CLOSE_TICKS)
    cstreak = ostreak = 0
    presses = 0
    ever_closed = False
    last_press = -999.0

    def _do_close():
        nonlocal presses, last_press
        print(f"  [Action] 按C关闭面板（第{presses+1}次）")
        _key(sess, "c", 220)
        presses += 1
        last_press = time.time()

    _panel_debug_save(det, _dbg, "before", start)
    if not _panel_closed_now(det):
        print("  [Action] 面板开着，准备关闭...")
        _do_close()
        time.sleep(0.2)
    else:
        print("  [Action] 面板已关闭，直接发送")
    while time.time() - start < timeout and not shutdown_event.is_set():
        closed = _panel_closed_now(det)
        now = time.time()
        if closed:
            cstreak += 1
            ostreak = 0
            ever_closed = True
            if cstreak >= need:
                print(f"  [Action] 面板已稳定关闭（连续{need}次确认）")
                _panel_debug_save(det, _dbg, "end_ok", start)
                return True
        else:
            ostreak += 1
            cstreak = 0
            if (ostreak >= need and presses < max_press and not ever_closed
                    and now - last_press > press_cooldown):
                _do_close()
                ostreak = 0
        if _dbg and _dbg_ticks and (time.time() - start) >= _dbg_ticks[0]:
            _panel_debug_save(det, _dbg, "mid", start)
            _dbg_ticks.pop(0)
        time.sleep(gap)
    print(f"  [Action] {timeout:.0f}s 内未能确认面板关闭（共按C {presses} 次）")
    _panel_debug_save(det, _dbg, "end_fail", start)
    return False


# 发送策略：1=面板开着直接发（路线A，2026-09-08 真机实测，默认）；0=旧路线（先C关面板再发）。
# 真机实测：历史面板开着时 回车=激活输入框、打字、回车=发送，发完面板仍在；而先C关面板会因
# 状态检测滞后出现“第1下C已关、被判没关、补按第2下C又打开”（肉眼一开一关）最终停在开发不出。
SEND_KEEP_PANEL = os.environ.get("SKY_SEND_KEEP_PANEL", "1") != "0"
# 发完是否补一下 ESC 退出输入框激活态（对齐上游 akinia；=0 关闭，走纯固定时序）
SEND_EXIT_INPUT_ESC = os.environ.get("SKY_SEND_EXIT_INPUT_ESC", "1") != "0"
# 动作（姿势/抱抱）后是否主动重开聊天面板（=0 关闭，回到只靠守门 1.2s 后重开）
REOPEN_PANEL_AFTER_ACTION = os.environ.get("SKY_REOPEN_AFTER_ACTION", "1") != "0"


def _send_msg(det, sess, msg, state) -> bool:
    global _last_gohome_time, _sending_msg
    # 回遇境后15秒内不打开聊天框（避免干扰回遇境过程）
    if time.time() - _last_gohome_time < GOHOME_CHAT_COOLDOWN:
        remaining = GOHOME_CHAT_COOLDOWN - (time.time() - _last_gohome_time)
        print(f"  [Action] 回遇境冷却中（剩余{remaining:.0f}秒），跳过发送消息: {msg}")
        return False
    deadline = time.time() + 30.0
    while time.time() < deadline and not shutdown_event.is_set() \
            and det.world.snapshot().screen == Screen.LOADING:
        time.sleep(0.3)

    if not SEND_KEEP_PANEL:
        # 旧路线（默认已不走，留作一键回退，SKY_SEND_KEEP_PANEL=0）：先把历史面板确实关掉再发
        _sending_msg = True
        try:
            if not _ensure_panel_closed_for_send(det, sess):
                print("  [Action] 警告：无法确认面板关闭，放弃发送（不在面板开着时按回车）")
                return False
            time.sleep(0.3)
            if not _panel_closed_now(det):
                print("  [Action] 发送前复查面板仍开着，放弃本次发送")
                return False
            for i in range(6):
                try:
                    mcp("send_chat", {"message": msg, "backend": BACKEND,
                                      "open_delay_ms": 260}, session=sess)
                    break
                except RuntimeError as e:
                    if ("前台" in str(e) or "foreground" in str(e).lower()) and i < 5:
                        if i == 0:
                            print("  [Action] 游戏不在前台，等回前台再发…")
                        time.sleep(5)
                        continue
                    print(f"  [Action] 发送失败: {e}")
                    return False
            time.sleep(0.5)
        finally:
            _sending_msg = False
        print("  [Action] 发送后打开聊天面板")
        _key(sess, "c", 220)
        time.sleep(1.0)
        return True

    # ── 路线A（默认，对齐上游 akinia）：确保面板开 → 回车/粘贴/回车 → 发完 ESC 退输入态 ──
    # 独占锁覆盖整个序列，期间 watch/reopen 不会插键（action 单线程 FIFO + not _sending_msg）。
    # 1) 发送前若面板没明确开着，用守门同一套逻辑打开（ever_opened 防抖、绝不重复 toggle）；
    #    打不开不硬放弃——send_chat 第一个回车在纯 HUD 下也能"打开面板+输入框"，由它兜底。
    if panel_open(det) is not True:
        _open_panel(det, sess, state)
    _sending_msg = True
    try:
        for i in range(6):
            try:
                # send_chat 内部：tap回车激活输入框 → open_delay 等光标起来 → 粘贴 → tap回车发送。
                # open_delay 260→340：真机掉帧时给输入框激活留足时间，防第二个回车打空/消息没发出。
                mcp("send_chat", {"message": msg, "backend": BACKEND,
                                  "open_delay_ms": 340}, session=sess)
                break
            except RuntimeError as e:
                if ("前台" in str(e) or "foreground" in str(e).lower()) and i < 5:
                    if i == 0:
                        print("  [Action] 游戏不在前台，等回前台再发…")
                    time.sleep(5)
                    continue
                print(f"  [Action] 发送失败: {e}")
                return False
        time.sleep(0.2)  # 等发送落定
        # 2) 发完补一下 ESC 退出"输入框激活态"回到历史面板（此刻刚激活过输入框，ESC=退输入态、
        #    不呼设置；历史面板保持开着继续 OCR），对齐上游 _send_msg。若真机发现这一下反而把
        #    面板收起，设 SKY_SEND_EXIT_INPUT_ESC=0 关闭，回到续31 纯固定时序。
        if SEND_EXIT_INPUT_ESC:
            _key(sess, "escape", 100)
            time.sleep(0.15)
    finally:
        _sending_msg = False
    # 全程不按 C：面板开着正是 OCR 需要的状态；偶发被收起由下一轮 watch 守门统一打开，
    # 绝不在发送序列里 toggle C（避免一开一关，或在输入栏按 C 把字母打进框里卡住）。
    print("  [Action] 已发送（保持面板开着" + ("、ESC退输入态" if SEND_EXIT_INPUT_ESC else "") + "）")
    return True


def _wait_arrive(det, timeout: float, target: Screen | None = None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if shutdown_event.is_set():
            return False
        snap = det.world.snapshot()
        if target is not None and snap.screen == target:
            return True
        if target is None and snap.screen in (Screen.IN_WORLD, Screen.HOME):
            return True
        time.sleep(0.3)
    return False

# ===================== 动作（接弹窗 / 回家） =====================

def _confirm_action(det, state, sess):
    global _confirm_in_progress, _confirm_lock_time, _friend_tree_last_time
    # 好友树动作刚结束的界面残留期：OCR 易把好友树/过渡画面误读成"前往"等确认词，
    # 此时并没有真正的传送弹窗，直接忽略，绝不按空格
    if time.time() - _friend_tree_last_time < FRIEND_TREE_CONFIRM_GUARD:
        print(f"  [Action] 好友树动作刚结束 {time.time()-_friend_tree_last_time:.1f}s，"
              f"忽略疑似确认弹窗（防界面残留误按空格）")
        return
    snap = det.world.snapshot()
    print(f"  [Action] 接弹窗: kw={'/'.join(snap.confirm_keywords)}")
    snap = det.world.snapshot()
    if not snap.confirm_dialog.value:
        print("  [Action] 弹窗已经不在了，取消接受")
        return
    # 立即设置标志，阻止其他逻辑干扰
    _confirm_in_progress = True
    _confirm_lock_time = time.time()
    # 策略：默认选中的是确认（√），直接按空格，不按方向键
    time.sleep(0.5)
    print("  [Action] 按空格确认（默认确认）")
    _key(sess, "space", 100)
    time.sleep(2.0)
    _confirm_in_progress = False

    deadline = time.time() + 8.0
    saw_loading = False
    while time.time() < deadline and not shutdown_event.is_set():
        if det.world.snapshot().screen == Screen.LOADING:
            saw_loading = True
            break
        time.sleep(0.3)
    if not saw_loading:
        print("  [Action] 8秒没见到传送过场，不按F，牵手交给伸手")
        return
    if not _wait_arrive(det, 25.0):
        print("  [Action] 25秒没落地，放弃后续动作")
        return
    print("  [Action] 到了！等伸手（f_hand 自动接）")


def _go_home_action(det, state, sess):
    now = time.time()
    with state.lock:
        last = state.last_gohome_time
    if now - last < GOHOME_COOLDOWN:
        print(f"  [Action] {int(now - last)}s 前刚回过家，这次忽略（防循环保险丝）")
        return
    with state.lock:
        state.last_gohome_time = now
    if not _close_panel(det, sess):
        print("  [Action] 面板可能还开着，不影响回家，继续")
    print("  [Action] 回家...")
    _key(sess, "8", 80);       time.sleep(0.5)
    _key(sess, "space", 100);  time.sleep(1.0)

    if _wait_arrive(det, 20.0, target=Screen.HOME):
        print("  [Action] 到遇境了！")
        time.sleep(1.2)
        _key(sess, "s", 900); time.sleep(0.5)
        _key(sess, "s", 900); time.sleep(0.5)
        print("  [Action] 已退开星盘，等过来牵手")
    else:
        print("  [Action] 没等到遇境画面，不退步（防 s 变打字）")


def _friend_tree_action(det, sess, state, act):
    """好友树互动动作（如抱抱）：检测入口图标 → 按F开好友树 → 方向键导航 → 空格发起 → ESC收起。
    导航序列来自 FRIEND_TREE_NAV，按动作名配置。返回是否成功发起。

    真机事实（2026-09-09 用户实测，以此为准，覆盖续36/续37 的旧假设）：聊天面板【开着】
    时直接按 F，光遇会自动关掉聊天框并打开好友树——所以不必、也不应先按 C 关面板
    （C 是开关键，叠加检测滞后会一开一关、卡在"关不掉"而放弃，前几次抱抱都栽在这）。
    前提：发送消息末尾已用 ESC 退出"打字光标激活态"（SEND_EXIT_INPUT_ESC），保证此刻
    是历史面板态、F 作为交互键而不是被打成字母 f。"""
    global _friend_tree_busy, _friend_tree_last_time
    nav = friend_tree_nav_keys(act)
    if not nav:
        print(f"  [Action] 未知好友树动作: {act}")
        return False
    if _yolo_model is None:
        print(f"  [Action] YOLO 未启用，无法检测好友树入口图标，跳过{act}")
        return False
    # 面板开着也直接做：按 F 时光遇会自动关聊天框、开好友树（真机实测），不再先按 C。
    _friend_tree_busy = True
    try:
        # 1. 等好友树入口图标出现（YOLO open_tree_icon；面板开着也能检测到，图标在角色旁）
        print(f"  [Action] 好友树动作: {act}，等入口图标...")
        deadline = time.time() + 5.0
        while time.time() < deadline and not shutdown_event.is_set():
            if _yolo_open_tree_detected:
                break
            time.sleep(0.15)
        else:
            print("  [Action] 没检测到好友树入口图标，不盲按F")
            return False

        # 2. 按F打开好友树（面板开着也按，游戏自动关聊天框、开好友树，真机实测）。
        #    发送末尾刚用 ESC 退打字输入态，这里先停 0.4s 让退输入态生效，避免 F 被当成
        #    字母打在输入框里（真机 9/10 第一次失败就是发送后立刻按 F、之后4秒界面纹丝不动）。
        time.sleep(0.4)

        def _press_f():
            """对着好友树入口图标按 F：先过光翼安全窗（f_prompt_press_allowed），
            被拒时返回 False 且**一个键都不按**（见第七十章 P0：open_tree_icon 与
            wing_light 会在同一枚图标上摇摆，硬门那一拍未必亮）。"""
            ok, why = f_prompt_press_allowed()
            if not ok:
                print(f"  [Guard] 拒绝按 F：{why} —— {act} 动作中止，未按任何键")
                return False
            _key(sess, "f", 120)
            return True

        # 首按前采一次"底噪"主动分（场景差异大：真机实测 0.08~0.57），供"相对增量"门限使用
        _sc_base, _ = det.friend_tree_score_now()
        if _sc_base is not None and _sc_base < 0:
            _sc_base = None
        if not _press_f():
            return False
        _last_f_t = time.time()
        _f_presses = 1

        # 3. 确认好友树真打开。主动、同步对最新帧跑好友树模板，不等每 3 帧才跑的后台模板；
        #    后台 friend_tree 通道作 OR 双保险。补按 F 的判据与门限依据见上方常量注释：
        #    入口图标不再当"没打开"的证据（树打开后它仍恒真），改由"等够时间 + 没看到 F
        #    已生效的证据 + 图标仍在"决定是否补按；9/14 那次 2.6s 就补导致的"先牵上手/
        #    跟随、然后才导航到抱抱"由此消除。
        deadline = time.time() + FRIEND_TREE_WINDOW
        opened = False
        _last_dbg = 0.0
        _ft_thr = getattr(det.cfg, "template_threshold", 0.82)
        _sc_max = -1.0      # 首按后主动分最高值
        _chan_ever = False  # 后台通道是否曾为真
        while time.time() < deadline and not shutdown_event.is_set():
            _sc, _tn = det.friend_tree_score_now()
            _chan = det.world.snapshot().friend_tree.value
            if _sc is not None and _sc >= 0:
                _sc_max = max(_sc_max, _sc)
            if _chan:
                _chan_ever = True
            if _sc >= _ft_thr or _chan:
                opened = True
                print(f"  [Action] 好友树已识别（主动模板分={_sc:.2f} 后台通道={_chan} 按F{_f_presses}次），开始导航")
                break
            _nowt = time.time()
            if friend_tree_repress_plan(_f_presses, _nowt - _last_f_t,
                                        _sc_max, _sc_base, _chan_ever,
                                        _yolo_open_tree_detected):
                _f_presses += 1
                print(f"  [Action] 第{_f_presses - 1}下F后{_nowt - _last_f_t:.1f}s仍无反应"
                      f"（主动分{_sc:.2f} 最高{_sc_max:.2f}、入口图标仍在），补按第{_f_presses}次F")
                if not _press_f():
                    break
                _last_f_t = time.time()
            if _nowt - _last_dbg >= 0.5:
                _last_dbg = _nowt
                print(f"  [Action] 等好友树打开... 主动分={_sc:.2f}(阈值{_ft_thr}) 最高{_sc_max:.2f} "
                      f"后台tree={_chan} chat_open={panel_open(det)}")
            time.sleep(0.15)
        if not opened:
            print("  [Action] 按F多次后仍没检测到好友树打开，ESC兜底退出")
            _key(sess, "escape", 120)
            return False

        # 4. 方向键导航到目标节点
        for k in nav:
            _key(sess, k, 80)
            time.sleep(0.3)

        # 5. 空格发起。按之前再同步确认一次好友树仍在：非面板状态下 space 在光遇里是跳跃，
        #    若树其实已收起/没稳住，空格会打到游戏主界面（角色跳一下、方向键再把镜头带偏）。
        #    主动分连采最多 3 帧取最高（导航高亮切换的瞬间单帧可能偏低），后台通道只在
        #    无模板/无帧时兜底（它 TTL 4s 滞后，不能给"已收起"背书）。
        _sc_now, _chan_now, _probe = -1.0, False, 0
        for _ in range(3):
            _s, _ = det.friend_tree_score_now()
            _chan_now = det.world.snapshot().friend_tree.value
            _sc_now = max(_sc_now, _s if _s is not None else -1.0)
            _probe += 1
            if friend_tree_still_open(_sc_now, _chan_now):
                break
            time.sleep(0.15)
        if not friend_tree_still_open(_sc_now, _chan_now):
            print(f"  [Action] 空格发起前复查：好友树已不在（主动分={_sc_now:.2f} 后台通道={_chan_now} "
                  f"采样{_probe}次），放弃空格、ESC 兜底退出")
            _key(sess, "escape", 120)
            return False
        print(f"  [Action] {act} 导航完成，空格发起（复查主动分={_sc_now:.2f} 后台通道={_chan_now}）")
        _key(sess, "space", 100)
        time.sleep(1.0)

        # 6. ESC 收起好友树
        _key(sess, "escape", 120)
        time.sleep(0.5)
        print(f"  [Action] {act} 已发起，好友树已收起")
        return True
    finally:
        _friend_tree_busy = False
        _friend_tree_last_time = time.time()
        # 发起好友树互动（抱抱等）会让光崽站起来，姿势重置为 standing（真机实测）
        with state.lock:
            if state.pose_state != 'standing':
                print(f"  [Action] {act} 后光崽站起来，姿势重置为 standing")
                state.pose_state = 'standing'
                state.pose_update_time = time.time()


ACT_KEYS = {
    '点火': '1', 
    '收火': '1', 
    '鞠躬': '2',
    '回遇境': '5',
}

# 姿势动作映射：动作名 -> 目标状态
# 光遇坐下表情循环：站(0) -> 蹲(1) -> 坐(2) -> 躺(3) -> 站(0)
POSE_STATES = {'standing': 0, 'crouching': 1, 'sitting': 2, 'lying': 3}
POSE_ACTIONS = {'蹲下': 'crouching', '坐下': 'sitting', '躺下': 'lying'}


def _do_pose_action(det, sess, state, target_pose, current_pose=None, label=""):
    """执行姿势切换：从 current_pose（默认读 state.pose_state）按动作键 3 到 target_pose。
    按键间隔加长（0.7s），避免掉帧丢键导致少按一档（想躺只坐）。返回最终姿势。"""
    with state.lock:
        if current_pose is None:
            current_pose = state.pose_state
        state.pose_last_target = target_pose
    current_idx = POSE_STATES.get(current_pose, 0)
    target_idx = POSE_STATES.get(target_pose, 0)
    # 计算需要按几次3（4次一个循环：站->蹲->坐->躺->站）
    press_count = (target_idx - current_idx + 4) % 4
    if press_count == 0:
        print(f"  [Action] {label or target_pose} -> 已经是{target_pose}，跳过")
        with state.lock:
            state.pose_state = target_pose
        return target_pose
    if not _close_panel(det, sess):
        print(f"  [Action] 面板可能还开着，{label or target_pose} 是游戏键，照做")
    print(f"  [Action] {label or target_pose} -> 当前{current_pose}，目标{target_pose}，按{press_count}次3")
    for i in range(press_count):
        _key(sess, "3", 80)
        time.sleep(0.7)
    final_pose = target_pose
    if VISION_POSE_CHECK and _vision.available:
        final_pose = _verify_pose_with_vision(det, sess, target_pose)
    with state.lock:
        state.pose_state = final_pose
        state.pose_update_time = time.time()
        state.pose_need_retry = False
        # 光遇中坐下/蹲下/躺下会自动断开牵手，同步重置牵手状态
        if state.is_holding_hands:
            print(f"  [Action] 姿势动作{label or target_pose}会断开牵手，重置牵手状态")
            state.is_holding_hands = False
    print(f"  [Action] 姿势状态更新为: {final_pose}")
    return final_pose

# 好友树互动动作：动作名 -> 打开好友树后的方向键导航序列（按动作名可配置，便于以后加击掌等）
# 【后期主动发起动作的前提】要让星河主动发起摸头/打闹/熊抱/公主抱/共舞等更多互动，
# 必须先在游戏里把「陪伴号（星河的小号）与玩家的好友树」全部解锁——未解锁动作在好友树里
# 是灰色/蓝色蜡烛，方向键导航过去也发不出。全解锁后，以"底行左1牵手"为起点数出每个动作
# 的方向键序列，补进下面这个表即可（无需改检测/执行其它代码）。
FRIEND_TREE_NAV = {
    # 真机实跑确认（2026-09-10，首次真正走通导航）：默认焦点在【最底行左1"发起牵手"】，
    # right 1 次到同底行第2列，再连续 up 3 次到"从上数第2行中间"的1级抱抱（浅色已解锁）。
    # 旧序列 up×4 会多走一格、冲到最顶行第2列那个蓝色蜡烛5未解锁动作（真机 21:21 实证）。
    # 网格（列左1~3、行从底R1到顶R5）：R1 牵手/高五；R2 礼物/碰拳/2级；R3 3级/牵手/2级；
    # R4 聊天/【抱抱】/2级；R5 并肩/蜡5(未解锁)。以后加动作都以"底行左1牵手"为起点数，只改这里。
    '抱抱': ['right', 'up', 'up', 'up'],
}


def friend_tree_nav_keys(act):
    """返回动作在好友树里的方向键导航序列；未收录返回 None。"""
    return FRIEND_TREE_NAV.get(act)


# ===== 好友树动作：F 生效判定 / 补按节奏 / 空格前复查（2026-09-14 真机标定，见续45/续46）=====
# 为什么重新设计"补按 F"的判据：
#   1) 入口图标信号 _yolo_open_tree_detected 在好友树已打开后仍几乎恒真——头顶那个圆形 F
#      图标只是从"好友树入口"变成"跟随/牵手"，YOLO 的 open_tree_icon 类别不变，9/14 日志里
#      连续几百帧 0.83~0.93。它不能再用来证明"树没开"，只保留"图标不在就不盲按"这个必要条件。
#   2) 主动模板分在真机两次里都全程平在 0.35~0.40、直到突然跳到 0.96（"F 真没生效"那次是
#      恒定 0.40），所以"分数没起来"也不等于"F 没生效"，单靠分数门限拦不住误按。
#   3) 真正防误按的是等待时间：F→树被识别耗时实测 1.5~2.0s（9/10 21:04）、2.5~3.0s
#      （9/10 21:17）、5.4~6.9s（9/14 21:50 误按那次）。故首补等待取 8.0s（> 6.9s 留余量），
#      后续补按间隔 2.5s，含首次最多 3 次、总观察窗口 13s。
# 门限取值依据（离线标定，走真实 PanelDetector.friend_tree_score_now）：
#   树稳定开 1080p 整帧 0.917~0.998；无反应帧 0.193/0.382；非树对照 80 张聊天帧 0.191~0.571
#   （中位 0.371）→ 绝对门限 0.65 高于非树上限 0.571、低于确认阈值 0.82；
#   相对增量 0.20 参照"失败帧增量 0.00 / 成功帧增量 0.88 / 同场景抖动 <0.05"。
FRIEND_TREE_EXPAND_MIN = 0.65         # 绝对"正在展开"门限
FRIEND_TREE_EXPAND_DELTA = 0.20       # 相对首按前底噪的增量门限（免疫场景底噪差异）
FRIEND_TREE_REPRESS_WAIT_FIRST = 8.0  # 首次补按前的最短等待（> 真机最慢识别 6.9s）
FRIEND_TREE_REPRESS_WAIT_NEXT = 2.5   # 后续补按间隔
FRIEND_TREE_MAX_PRESS = 3             # 含首次最多按 F 次数
FRIEND_TREE_WINDOW = 13.0             # 等待好友树打开的总观察窗口
FRIEND_TREE_STILL_OPEN_MIN = 0.65     # 空格发起前"树仍在"门限（比确认阈值 0.82 宽松）


def friend_tree_f_effective(sc_max, sc_base, chan_ever,
                            expand_min=FRIEND_TREE_EXPAND_MIN,
                            expand_delta=FRIEND_TREE_EXPAND_DELTA):
    """纯决策：第一下 F 是否已生效（树正在展开/已开）——生效则【永不再补按 F】。
    三条任一成立即认定生效：后台通道曾为真 / 主动分爬到绝对门限 /
    主动分比首按前底噪高出增量门限。分数不可用（<0，无模板或无帧）时不作证据。"""
    if chan_ever:
        return True
    if sc_max is None or sc_max < 0:
        return False
    if sc_max >= expand_min:
        return True
    if sc_base is not None and sc_base >= 0 and (sc_max - sc_base) >= expand_delta:
        return True
    return False


def friend_tree_repress_wait(presses,
                             wait_first=FRIEND_TREE_REPRESS_WAIT_FIRST,
                             wait_next=FRIEND_TREE_REPRESS_WAIT_NEXT):
    """纯决策：已按过 presses 次之后，距下一次补按需要等多久（秒）。
    presses<=1（只按过首次）用首补等待，之后用常规间隔。"""
    return wait_first if presses <= 1 else wait_next


def friend_tree_repress_plan(presses, since_last_f, sc_max, sc_base, chan_ever,
                             icon_present,
                             max_press=FRIEND_TREE_MAX_PRESS,
                             wait_first=FRIEND_TREE_REPRESS_WAIT_FIRST,
                             wait_next=FRIEND_TREE_REPRESS_WAIT_NEXT,
                             expand_min=FRIEND_TREE_EXPAND_MIN,
                             expand_delta=FRIEND_TREE_EXPAND_DELTA):
    """纯决策：现在要不要补按 F。四个条件全满足才补——
    未到按次上限、距上次 F 等够（首补 8.0s / 后续 2.5s）、没看到任何"F 已生效"的证据、
    入口图标仍在（图标不在时不盲按）。"""
    if presses >= max_press:
        return False
    if since_last_f < friend_tree_repress_wait(presses, wait_first, wait_next):
        return False
    if friend_tree_f_effective(sc_max, sc_base, chan_ever, expand_min, expand_delta):
        return False
    return bool(icon_present)


def friend_tree_still_open(sc_now, chan_now,
                           still_min=FRIEND_TREE_STILL_OPEN_MIN):
    """纯决策：导航结束、按空格发起之前，好友树是否仍在。
    主动分可用（>=0）时以它为准；后台通道 TTL 4s、树刚收起时还会真几秒，拿它给
    "已收起"背书会让空格打到游戏里（角色跳一下），故只在无模板/无帧（主动分<0）时兜底。"""
    if sc_now is not None and sc_now >= 0:
        return sc_now >= still_min
    return bool(chan_now)


_CHAT_TAG_RE = re.compile(r'\[CHAT\](.*?)\[/CHAT\]', re.DOTALL)


def extract_chat_texts(reply):
    """从 LLM 回复协议文本提取所有 [CHAT]..[/CHAT] 内的待发送文本（发送层安全门控）。
    没有 CHAT 标签时（思考原文/[IDLE]/乱码/只有[ACT]）一律返回空列表，保证任何未按
    协议输出的内容都不会被发到游戏；标签内为空白的跳过。"""
    if not reply:
        return []
    out = []
    for m in _CHAT_TAG_RE.finditer(reply):
        t = m.group(1).strip()
        if t:
            out.append(t)
    return out


_PROTOCOL_TAG_RE = re.compile(r'\[/?(?:CHAT|ACT|KEY|IDLE)\]')


def ensure_chat_wrapped(reply):
    """协议宽容兜底：模型完全没用任何协议标签、直接裸输出一句聊天时，自动包成
    [CHAT]..[/CHAT]（2026-09-08 实机：deepseek-v4-flash 直接回纯文本，而发送层只认
    [CHAT]标签，导致“判定要发却静默不回消息”）。只要出现任意 CHAT/ACT/KEY/IDLE 标签
    （含开/闭/畸形半截）就原样返回，交给 extract_chat_texts 安全门控，绝不把[ACT]指令、
    思考过程或畸形标签当聊天发出。"""
    if not reply:
        return reply
    text = reply.strip()
    if text and not _PROTOCOL_TAG_RE.search(reply):
        return f"[CHAT]{text}[/CHAT]"
    return reply


# ACT 标签的统一定义：发送层/动作分发/回遇境判定都从这里取，避免各写各的正则。
# 标签内允许空白（模型常输出 '[ACT] 回遇境 [/ACT]'），所以一律 group(1).strip() 后再比动作名。
_ACT_TAG_RE = re.compile(r'\[ACT\](.*?)\[/ACT\]', re.DOTALL)


def extract_act_names(reply):
    """提取回复里所有 [ACT]..[/ACT] 的动作名（已 strip 掉标签内空白）。

    没有 ACT 标签/回复为空时返回 []。历史上回遇境的判定用的是精确串
    `'[ACT]回遇境[/ACT]' in reply`，模型一旦多打空格就漏判、导致"先发消息再回遇境"
    绕过了保护（2026-10-05 修复），所以统一走这个函数。
    """
    if not reply:
        return []
    return [m.group(1).strip() for m in _ACT_TAG_RE.finditer(reply)]


def _execute_reply(det, state, sess, reply):
    # 先检查是否有回遇境动作，如果有就先执行动作，跳过消息发送
    has_gohome = '回遇境' in extract_act_names(reply)
    if has_gohome:
        print("  [Action] 检测到回遇境动作，跳过消息发送，先执行动作")
    else:
        for msg in extract_chat_texts(reply):
            print(f"  [Action] 发消息: {msg}")
            if _send_msg(det, sess, msg, state):
                with state.lock:
                    state.recent_seen.append((msg, time.time()))
                    state.sent_history.append((msg, time.time()))
            time.sleep(1.0)  # 发送完消息后延迟1秒再执行后续操作

    for act in extract_act_names(reply):
        if act == '回家牵手':
            if det.world.snapshot().screen == Screen.HOME:
                print("  [Action] 已经在遇境了，跳过回家")
                continue
            _go_home_action(det, state, sess)
            continue
        if act == '牵手':
            deadline = time.time() + 5.0
            while time.time() < deadline and not shutdown_event.is_set():
                snap = det.world.snapshot()
                if snap.f_prompt.value and snap.f_prompt_name.startswith("f_hand"):
                    print("  [Action] 牵手图标在，按 F 牵住")
                    _key(sess, "f", 120)
                    with state.lock:
                        state.last_fhand_time = time.time()
                    break
                time.sleep(0.2)
            else:
                print("  [Action] 没等到牵手图标，不盲按 F")
            continue
        # 好友树互动动作（抱抱等）：检测入口图标→F→导航→空格→ESC
        if act in FRIEND_TREE_NAV:
            if _friend_tree_action(det, sess, state, act):
                _reopen_panel_after_action(det, sess, state)
            continue
        # 姿势动作：智能切换，根据当前状态计算需要按几次3
        if act in POSE_ACTIONS:
            target_pose = POSE_ACTIONS[act]
            _do_pose_action(det, sess, state, target_pose, label=act)
            _reopen_panel_after_action(det, sess, state)
            continue

        k = ACT_KEYS.get(act)
        if not k:
            print(f"  [Action] 未知动作: {act}")
            continue
        if not _close_panel(det, sess):
            print(f"  [Action] 面板可能还开着，{act} 是游戏键，照做")
        print(f"  [Action] {act} -> {k}")
        if isinstance(k, list):
            for key in k:
                _key(sess, key, 80)
                time.sleep(0.4)
        else:
            _key(sess, k, 80)
            time.sleep(0.3)

        # 回遇境特殊处理：按快捷栏后等待确认对话框并立即确认
        if act == '回遇境':
            global _confirm_in_progress, _confirm_lock_time, _last_gohome_time
            # 记录回遇境开始时间，15秒内不打开聊天框
            _last_gohome_time = time.time()
            # 回遇境重试逻辑：最多重试2次
            # 每次：关闭聊天面板 → 按5 → 等对话框 → 按空格 → 等10秒过场
            gohome_success = False
            for retry in range(3):  # 最多执行3次（1次正常 + 2次重试）
                if retry > 0:
                    print(f"  [Action] 回遇境重试 ({retry}/2)")
                # 步骤1：确保聊天面板完全关闭
                print("  [Action] 确保聊天面板关闭...")
                close_attempts = 0
                while panel_open(det) is True and close_attempts < 3:
                    _key(sess, "c", 150)
                    time.sleep(0.8)
                    close_attempts += 1
                if panel_open(det) is True:
                    print("  [Action] 警告：聊天面板可能还开着，继续执行")
                # 步骤2：按快捷栏第5格（回遇境）
                print("  [Action] 按快捷栏第5格（回遇境）")
                _key(sess, "5", 80)
                time.sleep(0.5)
                # 步骤3：等待确认对话框出现（最多8秒）
                print("  [Action] 等待确认对话框...")
                confirm_found = False
                confirm_deadline = time.time() + 8.0
                while time.time() < confirm_deadline and not shutdown_event.is_set():
                    if det.world.snapshot().confirm_dialog.value:
                        confirm_found = True
                        break
                    time.sleep(0.15)
                if not confirm_found:
                    print("  [Action] 8秒内没检测到确认对话框，重试")
                    continue
                # 步骤4：按空格确认（只按一次，不重试）
                print("  [Action] 检测到确认对话框，按空格确认（只按一次）")
                _confirm_in_progress = True
                _confirm_lock_time = time.time()
                time.sleep(0.5)  # 等对话框稳定
                _key(sess, "space", 100)
                # 步骤5：等待传送过场（最多10秒）
                print("  [Action] 等待传送过场（最多10秒）...")
                loading_deadline = time.time() + 10.0
                saw_loading = False
                while time.time() < loading_deadline and not shutdown_event.is_set():
                    if det.world.snapshot().screen == Screen.LOADING:
                        saw_loading = True
                        print("  [Action] 见到传送过场，回遇境确认成功！")
                        break
                    time.sleep(0.3)
                _confirm_in_progress = False
                if saw_loading:
                    gohome_success = True
                    # 设置回遇境完成标志，让AI知道刚刚回到遇境
                    with state.lock:
                        state.gohome_completed = True
                    print("  [Action] 回遇境成功，已通知AI")
                    # 等待加载完成，主动打开聊天面板
                    print("  [Action] 等待加载完成...")
                    wait_deadline = time.time() + 30.0
                    while time.time() < wait_deadline and not shutdown_event.is_set():
                        if det.world.snapshot().screen != Screen.LOADING:
                            break
                        time.sleep(0.5)
                    time.sleep(2.0)  # 等游戏稳定
                    print("  [Action] 回遇境后打开聊天面板")
                    _key(sess, "c", 150)
                    time.sleep(1.2)
                    # 确认面板打开，没打开再试一次（守门在 HOME 场景也会兜底）
                    if panel_open(det) is not True:
                        print("  [Action] 面板还没打开，再按一次 C")
                        _key(sess, "c", 150)
                        time.sleep(1.2)
                    break
                else:
                    print("  [Action] 10秒内没见到传送过场，重新执行回遇境")
            if not gohome_success:
                print("  [Action] 回遇境失败（已重试2次）")

    for m in re.finditer(r'\[KEY\](.*?)\[/KEY\]', reply, re.DOTALL):
        raw = m.group(1).strip()
        km = re.match(r'([a-zA-Z\-]+)\s*(\d+)?', raw)
        if not km:
            continue
        k = km.group(1).lower()
        ms = min(int(km.group(2) or 80), 3000)
        if not _close_panel(det, sess) and k in ("enter", "return", "space"):
            print(f"  [Action] 面板没关掉，{k} 会打开聊天输入框，跳过")
            continue
        print(f"  [Action] 按键 {k} {ms}ms")
        _key(sess, k, ms); time.sleep(ms / 1000 + 0.05)

# ===================== 聊天 OCR（沿用 v6 调优管线） =====================

OCR_FIX = {
    '尔': '你', '咐': '吧', '巳': '已', '宄': '究',
    '迏': '达', '苎': '苦', '亇': '个', '対': '对', '呮': '只',
    '凊': '清', '莪': '我', '毎': '每', '飬': '养', '児': '儿',
    '経': '经', '気': '气', '関': '关', '臫': '自', '収': '收',
}

NAME_FIX = {
    '生人': '陌生人',
    '陌生': '陌生人',
    '陌牛人': '陌生人',
}


def fix(t):
    for w, r in OCR_FIX.items():
        t = t.replace(w, r)
    # 漏字修正：OCR 常把"星河"识别成"河"
    # 行首"河"+分隔符 → "星河"+分隔符
    t = re.sub(r'^河([\-－—~·•\s])', r'星河\1', t)
    # "河你好呀" → "星河你好呀"
    t = t.replace("河你好呀", "星河你好呀")
    # "们又见面了" → "我们又见面了"
    t = t.replace("们又见面了", "我们又见面了")
    # 词组级定向纠错（实测 OCR 把“舍”认成“啥”、“毕”认成“华”）：只纠这些在中文里
    # 不成词的错误组合，绝不动单字“啥”（否则“干啥/做啥”会被误伤）；也不再做单字
    # “舍→啥”的全局替换——那会把本来认对的“宿舍/舍友”反而改错（2026-09-03 实机定位）。
    t = t.replace("宿啥", "宿舍").replace("啥友", "舍友").replace("华竟", "毕竟")
    # ── 2026-10-15 真机实锤错字（均语境受限，不做单字全局替换，避免反向优化）──
    # 语气词"哼"被认成"亨"：仅当"亨"在句首/标点后且紧跟语气标点时才改；
    # 不动"大亨/亨利/亨通"等（它们"亨"在句中，或后面不跟语气标点）。
    t = re.sub(r'(^|[\s，。！？!?…~～,])亨(?=[！!~～,，。…？?\s])', r'\1哼', t)
    # 定向补一句首"亨星河"（"哼，星河自己说的"，"亨"后直接跟名字、无语气标点）
    t = re.sub(r'(^|[\s，。！？!?…~～,])亨星河', r'\1哼星河', t)
    # "好啦"被认成"子啦"（"子啦"不成词；"子"单字不碰，保护孩子/桌子/兔子）
    t = t.replace("子啦", "好啦")
    # 句末语气词"欸"(口+矣)被 OCR 按偏旁认成"俟"(亻+矣)/"埃"(土+矣)：
    # 玩家说的是"雨林欸"。雨林俟/雨林埃→雨林欸；林俟/林埃（"雨"也漏识）→雨林欸。
    # 交替长串优先，不产生雨雨林；只在"雨林"组合内纠，不碰尘埃/俟机等正常词。
    t = re.sub(r'雨林[俟埃]|林[俟埃]', '雨林欸', t)
    return t


def ocr_str(items):
    lines = []
    for t in items:
        text = fix_vocab(fix(t["text"].strip()))
        if not text or float(t.get("confidence", 0)) <= 0.35:
            continue
        # 过滤未解锁聊天玩家的"......"（OCR常误识别为"陌生人"）
        if "陌生人" in text:
            continue
        # 过滤纯省略号/点号行
        if all(c in ".·。…· " for c in text):
            continue
        lines.append(text)
    return "\n".join(lines)


_UI_SKIP = {
    '聊天', 'ESC', '退后', 'ENTER', '语音输入',
    'F', 'R', 'T', 'C', '发送', 'NTER', 'SPACE', 'ESCAPE',
}

_NAME_SEP_RE = re.compile(
    r'^(.+)\s*[\-‒–—―~〜－·•]\s*(\S+)$')
_NAME_ONLY_RE = re.compile(r'^[\-‒–—―~〜－·•]\s*(\S{1,16})$')


def _is_own_line(line: str, own_texts) -> bool:
    n = _PUNCT_RE.sub('', line)
    if not n:
        return False
    for t in own_texts:
        tn = _PUNCT_RE.sub('', t)
        if not tn:
            continue
        if n == tn or (len(n) >= 2 and n in tn) or _msg_similar(line, t):
            return True
    return False


# 说话人名字 OCR 误识显式归一（只作用于 extract 解析出的“名字字段”，不碰正文）
NAME_OCR_FIX = {
    # 幺幺 的常见误识
    '幺么': '幺幺', '么么': '幺幺', '幺玄': '幺幺', '公么': '幺幺', '公公': '幺幺',
    # 珂珂 的常见误识（珂 常被认成 可/河；两字全错时就近匹配救不回来，显式列出）
    '可可': '珂珂', '河河': '珂珂',
}
# 类分隔符：标准分隔符之外额外允许汉字“一”（OCR 常把横杠 - 认成 一），仅用于行尾已知名字兜底
_NAME_SEP_LIKE = set('-‒–—―~〜－·•一 ')


def _known_names():
    """当前可接受的说话人：AI 自己 + 白名单好友 + AI 名字。运行时读全局，bat 改白名单即时生效。"""
    return {x for x in (set(SELF_NAMES) | set(WHITELIST) | {AKI}) if x}


def _canonical_name(raw):
    """把 OCR 误识的说话人名字归一到已知名字；无法可靠归一时返回 None（不猜）。
    顺序：原样命中 -> 显式混淆表 -> 等长仅差1字 -> 长度差1且高相似。只用于名字字段。"""
    raw = (raw or '').strip()
    if not raw or '陌生人' in raw:
        return None
    known = _known_names()
    if raw in known:
        return raw
    fixed = NAME_OCR_FIX.get(raw)
    if fixed in known:
        return fixed
    for k in known:
        if len(k) >= 2 and len(k) == len(raw) and sum(a != b for a, b in zip(k, raw)) == 1:
            return k
    for k in known:
        if len(k) >= 2 and abs(len(k) - len(raw)) == 1 and                 SequenceMatcher(None, k, raw).ratio() >= 0.8:
            return k
    return None


def _split_by_known_name(line):
    """标准分隔符正则切不开时的兜底：从右向左找类分隔符（含“一”），其右侧 token 能归一到
    已知好友名才切分，返回 (content, canonical_name)，否则 None。
    强约束“右侧必须是已知名字”保证“一起跑图”这类正常句子不会被误切。"""
    for i in range(len(line) - 1, -1, -1):
        if line[i] in _NAME_SEP_LIKE:
            canon = _canonical_name(line[i + 1:].strip())
            if canon:
                head = line[:i].rstrip(''.join(_NAME_SEP_LIKE)).strip()
                if head:
                    return head, canon
    return None


def extract(text, own_texts=()):
    msgs = []
    pending = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line in _UI_SKIP or line.isdigit():
            continue
        if _is_own_line(line, own_texts):
            msgs.append(f"[{SELF_NAMES[0]}] {line}")
            pending = ""
            continue
        mo = _NAME_ONLY_RE.match(line)
        if mo or line == AKI:
            raw = AKI if line == AKI else mo.group(1)
            name = _canonical_name(raw) or NAME_FIX.get(raw, raw)
            if pending:
                msgs.append(f"[{name}] {pending.rstrip('-‒–—―~〜 ')}")
            pending = ""
            continue
        m = _NAME_SEP_RE.match(line)
        if m:
            content = m.group(1).strip()
            # 去掉内容尾部残留的重复/变体分隔符（中文双破折号 —— 或 OCR 多识别一个时）
            content = content.rstrip('-‒–—―~〜－·• ').strip()
            raw_name = m.group(2).strip()
            name = _canonical_name(raw_name) or NAME_FIX.get(raw_name, raw_name)
        else:
            # 兜底：标准分隔符切不开（如 OCR 把 - 认成“一”）时，仅当“行尾=类分隔符+已知好友名”才切
            kn = _split_by_known_name(line)
            if not kn:
                pending += line
                continue
            content, name = kn
        if pending:
            content = pending + content
            pending = ""
        if not content or not name:
            continue
        if all(c in '.·…●' for c in content):
            continue
        msgs.append(f"[{name}] {content}")
    return msgs


def _seen_before(state, msg: str, now: float) -> bool:
    state.recent_seen = [(m, t) for m, t in state.recent_seen if now - t < 120]
    return any(_msg_similar(msg, m) for m, t in state.recent_seen)


_NAME_PREFIX_RE = re.compile(r'^\[[^\]]*\]\s*')


def _is_self_msg(msg: str) -> bool:
    m = re.match(r'^\[([^\]]*)\]', msg)
    return bool(m) and m.group(1) in SELF_NAMES


_PUNCT_RE = re.compile(r'[\s,.!?~·…，。！？～、；;:：]')


def _msg_similar(a: str, b: str, threshold=0.6) -> bool:
    if not a or not b:
        return False
    a = _PUNCT_RE.sub('', _NAME_PREFIX_RE.sub('', a))
    b = _PUNCT_RE.sub('', _NAME_PREFIX_RE.sub('', b))
    if not a or not b:
        return a == b
    if min(len(a), len(b)) <= 3:
        return a == b
    return SequenceMatcher(None, a, b).ratio() > threshold


# ── OCR 增强：白色文字掩码 / 光遇专有词校正 / 跨帧在线投票（2026-09-01 学校离线新增）──

# 白色文字掩码阈值（HSV 的 V 下限、S 上限），针对“白字+深色描边+半透明底”
# 实机若亮背景漏字可下调 SKY_WHITE_VMIN；彩色噪点多可下调 SKY_WHITE_SMAX
WHITE_MASK_VMIN = int(os.environ.get("SKY_WHITE_VMIN", "165"))
WHITE_MASK_SMAX = int(os.environ.get("SKY_WHITE_SMAX", "90"))
# 跨帧投票开关与时间窗（秒）：首帧不等待，只用最近窗口内相似行做多数稳定
OCR_VOTE_ENABLED = os.environ.get("SKY_OCR_VOTE", "1") != "0"
OCR_VOTE_WINDOW = float(os.environ.get("SKY_OCR_VOTE_WIN", "5"))


def _white_text_mask(img_bgr):
    """提取高亮度、低饱和的白色文字像素，输出黑底白字单通道图。
    比直接灰度二值化更能压住云野/霞谷等亮色、彩色背景。"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([0, 0, WHITE_MASK_VMIN], dtype=np.uint8),
        np.array([179, WHITE_MASK_SMAX, 255], dtype=np.uint8),
    )
    # 2x2 闭运算补笔画小孔（比直接膨胀更不容易让相邻字粘连）
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


# 光遇专有名词：地图 / 常用黑话 / 关键物品。用于“等长仅差1字”的保守纠错，
# 不做全局替换，只有几乎完全匹配时才校正，避免误伤正常语句。
SKY_VOCAB = sorted(set([
    # 地图
    "遇境", "晨岛", "云野", "雨林", "霞谷", "暮土", "禁阁", "暴风眼", "伊甸",
    "圣岛", "圆梦村", "雪隐峰", "遗忘方舟", "藏宝岛礁", "音乐大厅", "办公室",
    "预言山谷", "大树屋", "风行网道",
    # 黑话 / 物品 / 动作
    "光翼", "翅膀", "烛火", "蜡烛", "白蜡", "红蜡", "升华蜡烛", "先祖", "复刻",
    "毕业", "跑图", "固玩", "监护人", "牵手", "点火", "收火", "献祭", "重生",
    "季节蜡烛", "斗篷", "裤子", "面具", "发型", "背背", "抱抱", "鞠躬", "充能",
    "回能", "能量", "大叫", "好友树", "星盘", "动作栏",
]), key=len, reverse=True)


def fix_vocab(line: str) -> str:
    """等长、仅差一个字时校正为光遇专有词（词长>=3才模糊纠错；2字词上下文太弱、
    易误伤正常用字，交给识别率与多帧投票兜底）。"""
    if not line:
        return line
    for w in SKY_VOCAB:
        L = len(w)
        if L < 3 or L > len(line):
            continue
        for i in range(len(line) - L + 1):
            seg = line[i:i + L]
            if seg == w:
                continue
            diff = sum(1 for a, b in zip(seg, w) if a != b)
            if diff == 1:
                line = line[:i] + w + line[i + L:]
    return line


class ChatVoteBuffer:
    """跨帧行级在线投票：首帧原样输出不延迟；之后用最近时间窗内的相似行
    做多数表决，压掉偶发的单帧错字。仅在 OCR 单线程内使用。"""

    def __init__(self, window: float = OCR_VOTE_WINDOW, max_hist: int = 400):
        self.window = window
        self.max_hist = max_hist
        self._hist = []  # [(text, conf, ts)]

    @staticmethod
    def _same_line(a: str, b: str) -> bool:
        if a == b:
            return True
        if abs(len(a) - len(b)) > 1:
            return False
        return SequenceMatcher(None, a, b).ratio() >= 0.8

    @staticmethod
    def _elect(group):
        cnt, confsum = {}, {}
        for t, c, _ in group:
            cnt[t] = cnt.get(t, 0) + 1
            confsum[t] = confsum.get(t, 0.0) + float(c)
        # 票数多者胜，平票取平均置信度高者
        return max(cnt, key=lambda t: (cnt[t], confsum[t] / cnt[t]))

    def stabilize(self, items, now: float):
        self._hist = [(t, c, ts) for t, c, ts in self._hist
                      if now - ts < self.window]
        out = []
        for it in items:
            cur = str(it.get("text", "")).strip()
            if not cur:
                continue
            conf = float(it.get("confidence", 0.0))
            group = [(cur, conf, now)]
            for t, c, ts in self._hist:
                if self._same_line(cur, t):
                    group.append((t, c, ts))
            best = self._elect(group)
            out.append({"text": best, "confidence": conf})
            self._hist.append((cur, conf, now))
        if len(self._hist) > self.max_hist:
            self._hist = self._hist[-self.max_hist:]
        return out


# 聊天OCR放大倍数：bench_ocr_3 实测（2026-09-06 干净1080p帧，同引擎配对A/B）放大3倍
# 比不放大单次/端到端慢约3倍（gray 1.7~1.9s -> 0.44~0.70s），识别行数(均13)与白名单关键
# 文本零损失（fx1/fx3 差异仅在被白名单过滤掉的"陌生人"占位行）。默认1=不放大；
# 若某台机器小字漏识别，可设环境变量 SKY_CHAT_OCR_SCALE=2 或 3 免改代码回退。
CHAT_OCR_SCALE = int(os.environ.get("SKY_CHAT_OCR_SCALE", "1"))


def crop_roi_3x(frame, roi):
    """按 ROI 比例裁剪聊天区并放大 CHAT_OCR_SCALE 倍（默认1=只裁剪不放大，证据见 bench_ocr_3）。"""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = roi
    img = frame[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
    if CHAT_OCR_SCALE == 1:
        return img
    return cv2.resize(img, None, fx=CHAT_OCR_SCALE, fy=CHAT_OCR_SCALE,
                      interpolation=cv2.INTER_CUBIC)


def build_ocr_variants(img):
    """由放大后的 ROI 构造全部 OCR 变体；eval_ocr.py 复用本函数，避免预处理逻辑漂移。
    返回 [(name, image), ...]，顺序即主链路尝试顺序。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)

    # 锐化：让文字边缘更清晰
    kernel_sharp = np.array([[-1, -1, -1],
                             [-1,  9, -1],
                             [-1, -1, -1]])
    gray_sharp = cv2.filter2D(gray_eq, -1, kernel_sharp)

    # 自适应二值化（应对不同背景亮度）
    adaptive = cv2.adaptiveThreshold(
        gray_eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 15, 8)
    # 白色文字掩码变体：专压亮色/彩色背景（白字+深色描边）
    white_mask = _white_text_mask(img)

    return [
        ("gray", gray_eq),
        ("sharp", gray_sharp),
        ("white", white_mask),
        ("color", img),
        ("th150", cv2.threshold(gray_eq, 150, 255, cv2.THRESH_BINARY)[1]),
        ("th180", cv2.threshold(gray_eq, 180, 255, cv2.THRESH_BINARY)[1]),
        ("adaptive", adaptive),
    ]


def read_chat_ocr(frame, ocr_engine, roi):
    img = crop_roi_3x(frame, roi)
    variants = build_ocr_variants(img)
    best, best_score = [], -1
    early_counts = []  # 前两个变体(gray/sharp)的识别行数
    for name, im in variants:
        result, _ = ocr_infer(ocr_engine, im)
        result = result or []
        if result:
            confs = [float(r[2]) for r in result]
            avg_conf = sum(confs) / len(confs)
            score = len(result) + avg_conf
        else:
            avg_conf = 0
            score = 0
        if score > best_score:
            best_score, best = score, result
        # gray 或 sharp 效果好就提前退出，省时间
        if name in ("gray", "sharp") and len(result) >= 3 and avg_conf > 0.60:
            break
        # gray、sharp 都识别出了较多行且数量接近（两种预处理结果一致、识别稳定）：
        # 不再跑后面 5 个变体——多人 20+ 行时这是主要耗时来源（曾表现为"多人卡死"）
        if name in ("gray", "sharp"):
            early_counts.append(len(result))
        if name == "sharp" and len(early_counts) == 2 \
                and min(early_counts) >= 3 \
                and abs(early_counts[0] - early_counts[1]) <= 2:
            break
    if not best:
        return []
    return [{"text": str(r[1]), "confidence": float(r[2])} for r in best]

# ===================== 对话压缩（改用 LLMClient） =====================

def trim(conv, llm_client):
    if len(conv) <= SUMMARY_TRIGGER:
        return conv
    cut = len(conv) - SUMMARY_KEEP
    old_text = "\n".join(m["content"] for m in conv[:cut])
    try:
        s = llm_client.chat(
            messages=[{"role": "user", "content": "用3-5句中文总结：\n" + old_text}],
            temperature=0.3, max_tokens=200,
        )
        print(f"  [AI] 压缩{cut}条 -> {s[:60]}...")
        r = conv[cut:]
        if r and r[0]["role"] == "user":
            r[0]["content"] = f"[摘要] {s}\n\n{r[0]['content']}"
        else:
            r.insert(0, {"role": "user", "content": f"[摘要] {s}"})
        return r
    except Exception as e:
        print(f"  [AI] 压缩失败: {e}")
        r = conv[-MAX_MSGS:]
        while r and r[0]["role"] != "user":
            r.pop(0)
        return r

# ===================== 记忆整理（移植自 sky-companion） =====================

def _maybe_update_memory(state, llm_client, force=False):
    """自动整理长期记忆：pending_turns >= 6 条时触发。"""
    if state.memory_updating:
        return
    if not force and not needs_update(state.memory, min_pending=MEMORY_UPDATE_MIN_PENDING):
        return
    pending = memory_pending_turns(state.memory, limit=16)
    if not pending:
        return

    state.memory_updating = True
    try:
        companion_name = "伴侣"
        old_profile = state.memory.get("profile_prompt", "") or "暂无。"
        prompt = build_memory_update_prompt(state.memory, companion_name, old_profile)
        print("  [Memory] updating...")
        result = llm_client.chat(prompt, temperature=0.2, max_tokens=MEMORY_UPDATE_MAX_TOKENS)
        summary = clean_memory_summary(result)
        if len(summary) >= 20:
            state.memory = update_profile(state.memory, summary)
            print("  [Memory] updated")
        else:
            print("  [Memory] skipped (too short)")
    except Exception as e:
        print(f"  [Memory] error: {e}")
    finally:
        state.memory_updating = False

# ===================== WatchThread =====================

def _guard_should_reopen(snap, panel_state, panel_closed_since, t0,
                         busy, sending, action_empty,
                         last_action, last_reopen):
    """守门：判断此刻是否应按 C 重新打开聊天面板（纯函数，便于离线测试）。
    只在 IN_WORLD / HOME 场景、面板持续关 PANEL_REOPEN_DELAY、且无弹窗/好友树/
    动作/发送/内存模式占用时才触发。HOME 场景必须包含——否则回遇境后面板一旦
    关闭就再也打不开（真机 2026-10-03 卡住根因）。"""
    if snap.screen not in (Screen.IN_WORLD, Screen.HOME):
        return False
    if panel_state is not False:
        return False
    if snap.confirm_dialog.value or snap.friend_tree.value:
        return False
    if panel_closed_since is None:
        return False
    if t0 - panel_closed_since <= PANEL_REOPEN_DELAY:
        return False
    if busy or sending or not action_empty:
        return False
    if MEM_READER_ENABLED:
        return False
    if t0 - last_action <= POST_ACTION_GRACE:
        return False
    if t0 - last_reopen <= REOPEN_COOLDOWN:
        return False
    return True


def _valid_name_text(text):
    """OCR 读到的名字是否像“有效玩家名”：含汉字，或连续≥2个英文字母；
    纯符号（? . 一 1）视为没读到，避免把噪点当陌生人名字。"""
    if not text:
        return False
    t = str(text).strip()
    if re.search(r"[一-鿿]", t):
        return True
    if re.search(r"[A-Za-z]{2,}", t):
        return True
    return False


def _accept_decision(ft_open, ft_busy, name_wl, name_text, vlm_obj):
    """纯决策（离线可单测）：互动邀请是否该按 F。阶段1只用于“模拟会不会按”打印，
    阶段3自动执行时复用。策略遵循用户意愿：读不到名字默认接受（不连累漏掉），
    但明确非白名单、或好友树开着、或传送/加入等危险界面时不按。"""
    # 纯符号/单字母噪声当作“没读到名字”，避免误判成陌生人
    if not _valid_name_text(name_text):
        name_text = ""
    if ft_open or ft_busy:
        return False, "好友树开着/好友树动作中"
    if name_text and not name_wl:
        return False, f"名字OCR读到非白名单'{name_text}'"
    if isinstance(vlm_obj, dict):
        if vlm_obj.get("danger_ui") is True:
            return False, "VLM判定为传送/加入/点火等危险界面"
        inv = str(vlm_obj.get("inviter_name") or "").strip()
        if (not name_text and inv and inv != "?"
                and vlm_obj.get("is_whitelist_name") is False):
            return False, f"VLM判定发起人非白名单'{inv}'"
    return True, "白名单或读不到名字（按默认接受）"


def _accept_vision_judge(frame, bbox):
    """VLM 看一眼互动邀请（可用才调）：发起人名字、动作、是否危险界面。返回 dict 或 None。"""
    if _vision is None or not getattr(_vision, "available", False) or frame is None:
        return None
    wl = "、".join(WHITELIST) if WHITELIST else "（未配置）"
    q = (
        "这是游戏《光·遇》画面。屏幕里可能有一个圆形的好友互动邀请图标（对方发起，"
        "通常要按 F 接受），动作是：抱抱hug / 背背carry / 击掌high_five / 碰拳fist_bump。\n"
        "请只返回 JSON、不要多余文字：\n"
        '{"inviter_name":"邀请图标上方显示的玩家名字，看不到就填 ?",\n'
        ' "is_whitelist_name": true或false,\n'
        ' "invite_action":"hug/carry/high_five/fist_bump/none",\n'
        ' "danger_ui": true或false,\n'
        ' "note":"一句话说明"}\n'
        f"is_whitelist_name：名字是否在白名单【{wl}】里；无法判断时填 false。\n"
        "danger_ui：画面其实是【传送/加入房间、点火、开门、收光翼、先祖、神坛、星盘、"
        "打开好友树】等其他带 F 的界面、而不是好友互动邀请时填 true。\n"
        "判断名字时以角色头顶或邀请图标上方的中文名为唯一依据。"
    )
    return _vision.ask_json(frame, q, max_tokens=200)


def _accept_invite_event(det, state, snap, frame, aname, cn, conf, bbox, t0, interact_q=None):
    """一次确认的互动邀请事件：名字 OCR + 界面状态 + 可选 VLM，打印观测/执行日志。
    阶段1（SKY_ACCEPT_AUTO=0）只打印、不按 F；阶段2（Delta B1）把事件喂 AI 先开口反应；
    阶段3在这里接拟人执行（按 F）。"""
    ft_open = bool(snap is not None and getattr(snap.friend_tree, "value", False))
    ft_busy = bool(_friend_tree_busy)
    try:
        popen = panel_open(det)
    except Exception:
        popen = None
    name_text = ""
    name_wl = False
    try:
        name_text = (ocr_player_name_above_icon(frame, bbox) or "").strip()
        name_wl = is_whitelist_player_name(name_text)
    except Exception as e:
        print(f"  [互动邀请] 名字OCR异常: {e}")
    vlm_obj = None
    try:
        vlm_obj = _accept_vision_judge(frame, bbox)
    except Exception as e:
        print(f"  [互动邀请] VLM判断异常(已忽略): {e}")
    would, reason = _accept_decision(ft_open, ft_busy, name_wl, name_text, vlm_obj)

    x1, y1, x2, y2 = (int(v) for v in bbox)
    vlm_txt = ""
    if vlm_obj:
        vlm_txt = (f" | VLM: 发起人={vlm_obj.get('inviter_name')} "
                   f"白名单={vlm_obj.get('is_whitelist_name')} "
                   f"动作={vlm_obj.get('invite_action')} 危险界面={vlm_obj.get('danger_ui')}")
    else:
        vlm_txt = " | VLM未启用(可用 SKY_VISION_ENABLED=1 开启，辅助判断发起人/危险界面)"
    mode = "自动模式" if ACCEPT_AUTO else "观测模式(不按F)"
    print(
        f"  [互动邀请] [{mode}] {cn}({aname}) conf={conf:.2f} 框=({x1},{y1},{x2},{y2})"
        f" | 名字OCR='{name_text or '?'}' 白名单={name_wl}"
        f" | 好友树开={ft_open} 忙={ft_busy} 面板={popen}{vlm_txt}"
    )
    print(f"  [互动邀请] 模拟决策: {'会按F' if would else '不按F'} —— {reason}")

    # Delta B1：只在“会接受”（白名单/默认接受）且开关开时，把邀请事件喂给 AI 先反应
    # （先开口说话、不按 F）。陌生人 / 好友树开 / 危险界面（would=False）一律不喂。
    if INTERACT_REACT_ENABLED and would and interact_q is not None:
        try:
            interact_q.put_nowait(interact_build_event(aname, cn, name_text, conf, t0))
            print(f"  [互动反应] 已喂给AI：{cn}（先开口反应，不按 F）")
        except queue.Full:
            print("  [互动反应] 事件队列满，本次跳过")

    # 阶段3：当 ACCEPT_AUTO 且 would 为真时，在此放入拟人执行（先台词/反应→延迟→按F）。
    # 阶段1 不放任何按键，确保观测零风险。


def _handle_accept_events(det, state, frame, t0, interact_q=None, action_q=None):
    """好友互动邀请事件：连续帧防抖 + 冷却去重 + 图标持续时长统计。
    在每次 YOLO 检测完成后调用（约 0.4s 一次，避免重复计同一帧）。
    interact_q 非空时，确认的白名单邀请事件会入队（Delta B1 喂 AI 先反应）。
    action_q 非空且 ACCEPT_AUTO 时，AI 卡住超 ACCEPT_FALLBACK_SECONDS 会投递兜底接受。"""
    global _accept_seen, _accept_event_time, _accept_present, _accept_first_seen
    global _accept_event_fed, _accept_miss, _accept_verdict
    try:
        snap = det.world.snapshot()
    except Exception:
        snap = None
    for aname, cn in ACCEPT_CLASSES.values():
        hit = _yolo_accept.get(aname)
        present = hit is not None and float(hit[0]) >= ACCEPT_CONF
        if present:
            _accept_miss[aname] = 0
            if not _accept_present[aname]:
                _accept_present[aname] = True
                _accept_first_seen[aname] = t0
                _accept_seen[aname] = 1
                _accept_event_fed[aname] = False
                _accept_verdict[aname] = "pending"  # 阶段3：图标出现，等AI决策
            else:
                _accept_seen[aname] += 1
        elif _accept_present[aname]:
            # 图标在屏但这一拍没检测到：先累计漏检，未达宽限视为仍在（不重置、不触发）
            _accept_miss[aname] += 1
            if _accept_miss[aname] < ACCEPT_MISS_FRAMES:
                continue
            # 连续漏检达宽限，认定真消失
            fs = _accept_first_seen.get(aname)
            dur = (t0 - fs) if fs else 0.0
            print(f"  [互动邀请] {cn}({aname}) 图标消失，持续约 {dur:.1f}s，"
                  f"累计检测 {_accept_seen[aname]} 拍（持续时长用于判断AI决策来不来得及）")
            _accept_present[aname] = False
            _accept_first_seen[aname] = None
            _accept_seen[aname] = 0
            _accept_event_fed[aname] = False
            _accept_miss[aname] = 0
            _accept_verdict[aname] = None  # 阶段3：邀请消失，清决策
            with state.lock:
                state.pending_invites.pop(aname, None)  # AI 据此知道邀请已取消
            continue
        else:
            continue
        # 事件级去重：确认防抖后，本次 present 只喂一次（图标持续不重复，图标消失才重置）
        if _accept_seen[aname] >= ACCEPT_CONFIRM_FRAMES and not _accept_event_fed[aname]:
            _accept_event_fed[aname] = True
            _accept_event_time[aname] = t0
            conf, bbox = hit
            with state.lock:
                state.pending_invites[aname] = {"action_cn": cn, "first_seen": t0}
            _accept_invite_event(det, state, snap, frame, aname, cn,
                                 float(conf), bbox, t0, interact_q)
        # 阶段3 兜底：事件已确认安全（喂给AI）、但 AI 一直没返回决策（verdict 仍 pending）
        # 超过 ACCEPT_FALLBACK_SECONDS，自动按 F 接受（不说话、不再延迟）。
        if (ACCEPT_AUTO and action_q is not None and _accept_event_fed[aname]
                and _accept_verdict[aname] == "pending"
                and _accept_first_seen[aname] is not None
                and (t0 - _accept_first_seen[aname]) >= ACCEPT_FALLBACK_SECONDS):
            print(f"  [阶段3] {cn}({aname}) AI 超过 {ACCEPT_FALLBACK_SECONDS:.0f}s 未决策，"
                  f"兜底自动接受（不说话）")
            _accept_verdict[aname] = "accept"
            action_q.put_nowait(("accept_invite", aname, cn, "fallback"))


# ===================== 拟人互动·阶段2（Delta B1）：邀请事件喂 AI 先反应 =====================
# 设计（离线实测，见 _tmp/_react_result.txt）：
#   - 动作类型【以 YOLO 为权威】（VLM 会把所有圆形 F 图标都判成 hug，8 张仅 2 对，不可信）；
#   - 名字 OCR 把关白名单；只在“会接受”的邀请触发，陌生人不理；
#   - 星河以人设先开口反应（台词），但 B1 绝不按 F（阶段3 才接受 + 15 秒兜底）。

# 当 LLM 针对邀请产出回复时，B1 只允许它“开口说话”，绝不允许任何 [ACT]/[KEY]/[IDLE]
# （B1 不按 F）。把回复强制清洗为纯 CHAT：有 CHAT 标签取第一句；裸文本（无任何标签）
# 兜底包成 CHAT；若含 ACT/KEY/IDLE 标签（模型想发动作）或拿不到有效文字，一律返回 None。
_INTERACT_NONCHAT_RE = re.compile(r'\[/?(?:ACT|KEY|IDLE)\]')


def interact_sanitize_to_chat(reply):
    if not reply:
        return None
    # 记忆 v4 保险：互动路径万一残留 RECALL 标签也剥掉，绝不让暗号出现在游戏输入框
    reply = _RECALL_TAG_RE.sub("", reply)
    chats = extract_chat_texts(reply)
    if chats:
        line = (chats[0] or "").strip()
        return f"[CHAT]{line}[/CHAT]" if line else None
    if _INTERACT_NONCHAT_RE.search(reply):
        return None
    bare = (reply or "").strip()
    if len(bare) >= 2:
        return f"[CHAT]{bare}[/CHAT]"
    return None


def interact_parse_decision(raw):
    """阶段3（B2）：解析 AI 对邀请的决策。返回 (decision, line)：
    decision = "accept"/"decline"/None；line=台词（可能为空，调用方用默认台词兜底）。
    - 含 [ACCEPT] -> accept；含 [DECLINE] -> decline；两者都出现时 accept 优先（安全侧=不卡住）。
    - 台词从 [CHAT] 取第一句。
    """
    if not raw:
        return None, ""
    has_accept = "[ACCEPT]" in raw
    has_decline = "[DECLINE]" in raw
    decision = "accept" if has_accept else ("decline" if has_decline else None)
    texts = extract_chat_texts(raw)
    line = texts[0] if texts else ""
    return decision, line


def interact_build_event(aname, cn, name_text, conf, t0):
    """构造一个邀请事件（喂给 ai_loop 处理）。"""
    return {
        "action": aname,
        "action_cn": cn,
        "name": (name_text or "").strip(),
        "conf": float(conf),
        "time": float(t0),
    }


# LLM 拿不到有效台词时的硬兜底（每种动作两句、不同情绪，随时间轮换，避免重复或清一色"怎么突然"）。
INTERACT_DEFAULT_LINES = {
    "抱抱": ["来，抱一下～", "抱抱充电，给你～"],
    "背背": ["想让我背你呀？上来～", "哟，这是要赖着我了～"],
    "击掌": ["来，击个掌！", "啪，默契满分～"],
    "碰拳": ["碰拳，成交～", "来，碰一个～"],
    "熊抱": ["哇，这么大一个拥抱～", "抱这么紧，想把我揉化呀～"],
    "谢幕": ["哟，还跟我谢幕呢～", "哈哈，这是在演哪一出～"],
    "花式击掌": ["这个花的我也会～", "来，看谁的更花～"],
    "握手": ["你好呀，小家伙～", "握手手，我们这算和好了吗～"],
    "摸头": ["唔，摸摸头好舒服～", "再摸摸，我又不躲～"],
    "打闹": ["哟，敢跟我闹？等着～", "来呀，我可不怕你～"],
    "搭肩": ["搭好了，靠过来吧～", "嗯，这样踏实多了～"],
    "共舞": ["要请我跳舞呀？走～", "哟，今天这么浪漫～"],
    "耳语": ["凑这么近，说什么悄悄话～", "嗯？我听着呢～"],
}


def interact_default_line(cn, seed=0):
    lines = INTERACT_DEFAULT_LINES.get(cn)
    if not lines:
        return "哟，这是要跟我玩什么呀～"
    try:
        return lines[int(seed) % len(lines)]
    except Exception:
        return lines[0]


def interact_invite_context(cn, who="", auto=False):
    """邀请反应的 system 追加规则（约束台词自然、不油腻、不生硬）。

    who=发起人名字（2026-10-19 修第六十三章 P1-2）：原来写死"珂珂"，但 SKY_WHITELIST 可配多人，
    别的白名单好友发起时 AI 会叫错人（与 user 消息里的真实名字打架）。who 为空时用中性表述。
    auto=阶段3（SKY_ACCEPT_AUTO=1）：让 AI 输出 [ACCEPT]/[DECLINE] 决策 + 台词；
    auto=False（阶段2）：只输出 [CHAT]，不按 F。
    """
    who_txt = (who or "").strip() or "有好友"
    base = (
        "\n\n## 现在有一个好友互动邀请\n"
        f"{who_txt}刚刚向你发起了【{cn}】。请你结合当前情境，自然地做出反应：\n"
        "- 先看“邀请之前的最近对话”：你们在聊什么、现在是什么心情（走心、开心、撒娇、安慰、要睡了、要走了……），"
        "让这句反应顺着刚才的话接下去，而不是没头没脑地惊讶。\n"
        "- 动作本身也带情绪：摸头、抱抱、搭肩多半是安抚、亲昵和陪伴；击掌、碰拳是默契与庆祝；打闹是想玩；"
        "共舞、耳语是想靠近；背背、熊抱是撒娇和热情。\n"
        "- 反应要自然、多样：可以是温柔承接、开心、撒娇、调皮、轻轻一笑，也可以是一句被戳中的心里话。"
        "不要每次都用“诶？怎么突然……”，那是套路，会显得很生硬、没在听对方说话。\n"
        "- 照情绪来：刚在说累、难过、迷茫，对方抱你或摸你头，就温柔接住（像“过来，给你充会儿电”“嗯，我在呢”）；"
        "说要睡了、要走、要下线，就轻轻道一句（像“晚安，摸摸头”“那我等你明天上线”）；"
        "平白无故、没什么铺垫时，才可以带一点小惊讶。\n"
        "- 用你平时说话的语气，口语化、简短自然，一次只说一两句话。\n"
        "- 你不是非得接受：你有拒绝的权利，也可以有小脾气；但这是你的自由而不是固定剧本，别刻意撒娇、别把“才不要～”当成套路。\n"
        "- 不要油腻、不要肉麻地堆甜言蜜语、不要说“抱完不许赖着不走”这种端着的话；也不要生硬客套。\n"
    )
    if auto:
        # 阶段3：AI 自己决定接不接受
        return base + (
            "- 这次请你自己决定要不要接受，并在台词前加一个决策标签：\n"
            "  · 愿意接受：输出 [ACCEPT][CHAT]你想说的话[/CHAT]（你会先说出这句话，停顿一下再按 F 接受）。\n"
            "  · 想逗一下/暂时不想：输出 [DECLINE][CHAT]你想说的话[/CHAT]（只说话，不会按 F）。\n"
            "- 对方是你信任的好友、没有危险时，通常倾向接受就好；拒绝留给你真的想逗一下或没心情的时候。\n"
            "- 不要输出 [ACT]/[KEY]，接受这件事程序会根据 [ACCEPT] 标签替你按 F。\n"
            "- 背景规则：如果你一直僵着不回应超过 15 秒，程序会自动帮你接受，所以别一直卡着。"
        )
    return base + (
        "- 这一次只输出 [CHAT]你想说的话[/CHAT]，不要输出任何 [ACT]/[KEY]，程序不会替你按 F。\n"
        "- 背景规则：如果你一直僵着不回应超过 15 秒，阶段3程序会自动帮你接受，所以别一直卡着。"
    )


def _handle_interact_event(det, state, action_q, llm_client, event):
    """在 ai_loop 线程处理一个邀请事件：以星河人设生成反应台词；阶段3（ACCEPT_AUTO）
    同时解析 AI 的 [ACCEPT]/[DECLINE] 决策，accept 时拟人执行按 F。
    邀请事件不进长期记忆（只是个事件），只把台词记入 my_words 防重复。"""
    global _accept_verdict
    aname = event.get("action") or ""
    cn = event.get("action_cn") or "抱抱"
    name = (event.get("name") or "").strip()
    who = (name + " ") if name else ""
    system_parts = [SYSTEM]
    if state.memory:
        system_parts.append("## 长期记忆\n" + memory_prompt(state.memory))
    system_parts.append(interact_invite_context(cn, name, auto=ACCEPT_AUTO))
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    # 最近对话窗口（让反应能接上上下文，而不是没头没脑地惊讶；2026-10-19 修"不顾上下文"）
    with state.lock:
        recent = list(state.conversation)[-6:]
    if recent:
        _role_zh = {"user": "玩家", "assistant": "你"}
        recent_txt = "\n".join(
            "%s：%s" % (_role_zh.get(m.get("role"), m.get("role")),
                        str(m.get("content", ""))[:120])
            for m in recent)
        messages.append({"role": "system",
                         "content": "## 邀请之前的最近对话（这是上下文，让你的反应能接上）\n"
                                    + recent_txt})
    user = (f"（系统事件，不是聊天）画面中{who}向你发起了【{cn}】邀请，"
            "圆形互动图标已经出现。请结合上面的对话自然地反应。")
    messages.append({"role": "user", "content": user})
    raw = ""
    try:
        raw = llm_client.chat(messages, temperature=0.85, max_tokens=120)
    except Exception as e:
        print(f"  [互动反应] LLM异常: {e}")
    # 解析决策与台词
    decision = None
    line = ""
    if ACCEPT_AUTO:
        decision, line = interact_parse_decision(raw)
        if decision is None:
            # AI 没给决策标签：白名单默认接受（不干等兜底），台词从回复里取
            decision = "accept"
            safe = interact_sanitize_to_chat(raw)
            if safe:
                t = extract_chat_texts(safe)
                if t:
                    line = t[0]
    else:
        safe = interact_sanitize_to_chat(raw)
        if safe:
            t = extract_chat_texts(safe)
            if t:
                line = t[0]
    if not line:
        line = interact_default_line(cn, seed=int(event.get("time", 0) or 0))
    try:
        line = polish_reply(line, recent_laugh_count(state.my_words))
    except Exception:
        pass
    if not line:
        line = interact_default_line(cn)
    state.my_words.append(line)
    if len(state.my_words) > 8:
        state.my_words.pop(0)
    # 兜底可能已在 AI 慢时把 verdict 改成 accept（并投了 fallback accept_invite）：
    # 此时只发台词，不重复投 accept_invite（避免重复按 F）。
    already_handling = ACCEPT_AUTO and _accept_verdict.get(aname) in ("accept", "done")
    if ACCEPT_AUTO and not already_handling:
        _accept_verdict[aname] = decision
    print(f"  [互动反应] {cn} -> {line}（决策={decision or '仅说话'}）")
    action_q.put(("execute", f"[CHAT]{line}[/CHAT]"))
    if ACCEPT_AUTO and decision == "accept" and not already_handling:
        action_q.put(("accept_invite", aname, cn, "normal"))


# 预览窗口：类别中文名 + 框颜色（BGR），键是模型 class id
_PREVIEW_CN = {0: "聊天框", 1: "牵手", 2: "好友树", 3: "抱抱", 4: "背背", 5: "击掌",
               6: "碰拳", 7: "跟随", 8: "光翼", 9: "点火", 10: "聚焦", 11: "打坐",
               12: "熊抱", 13: "谢幕", 14: "花式击掌", 15: "握手", 16: "摸头",
               17: "互火", 18: "打闹", 19: "搭肩", 20: "共舞", 21: "耳语"}
_PREVIEW_COLORS = {0: (170, 170, 170), 1: (0, 210, 0), 2: (255, 160, 0),
                   3: (200, 0, 200), 4: (0, 150, 255), 5: (255, 200, 0),
                   6: (60, 60, 255), 7: (255, 80, 180), 8: (0, 0, 255),
                   9: (0, 180, 255), 10: (200, 200, 0), 11: (0, 220, 220),
                   12: (180, 0, 120), 13: (128, 0, 128), 14: (0, 200, 100),
                   15: (160, 120, 0), 16: (200, 100, 255), 17: (0, 100, 255),
                   18: (0, 0, 180), 19: (100, 100, 100), 20: (255, 0, 128),
                   21: (180, 200, 0)}


def _draw_yolo_preview(det, now_t):
    """在主线程每帧调用：实时游戏画面叠加最近一次 YOLO 检测框。
    返回 False 表示用户按 q/关窗，要停止预览（不退出主程序）。"""
    frame = det.latest_frame()
    if frame is None:
        return True
    img = frame.copy()
    h, w = img.shape[:2]
    pil_ok = False
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", max(20, int(h * 0.022)))
        except Exception:
            font = ImageFont.load_default()
        for cls, conf, (x1, y1, x2, y2), name in _yolo_preview_boxes:
            bgr = _PREVIEW_COLORS.get(cls, (255, 255, 255))
            rgb = (bgr[2], bgr[1], bgr[0])
            d.rectangle([x1, y1, x2, y2], outline=rgb, width=3)
            label = f"{_PREVIEW_CN.get(cls, name)} {conf:.2f}"
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
        for cls, conf, (x1, y1, x2, y2), name in _yolo_preview_boxes:
            bgr = _PREVIEW_COLORS.get(cls, (255, 255, 255))
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), bgr, 2)
            cv2.putText(img, f"{name} {conf:.2f}", (int(x1), max(15, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, bgr, 2)
    # 顶部 HUD（英文/数字，避免 cv2 中文乱码）
    stale = now_t - _yolo_preview_time
    mode = "AUTO" if ACCEPT_AUTO else "OBSERVE"
    hud = (f"YOLO conf>={YOLO_CONF_THRESHOLD:.2f} | boxes:{len(_yolo_preview_boxes)} "
           f"| last detect:{stale:.1f}s ago | accept:{mode} | q=close")
    cv2.rectangle(img, (0, 0), (w, 34), (0, 0, 0), -1)
    cv2.putText(img, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (255, 255, 255), 2)
    if YOLO_PREVIEW_SCALE and YOLO_PREVIEW_SCALE != 1.0:
        img = cv2.resize(img, None, fx=YOLO_PREVIEW_SCALE, fy=YOLO_PREVIEW_SCALE)
    cv2.imshow(_PREVIEW_WIN, img)
    key = cv2.waitKey(30) & 0xFF
    if key in (ord('q'), ord('Q'), 27):
        return False
    try:
        if cv2.getWindowProperty(_PREVIEW_WIN, cv2.WND_PROP_VISIBLE) < 1:
            return False
    except Exception:
        pass
    return True


def watch_loop(det: PanelDetector, state: SharedState,
               ocr_q: queue.Queue, action_q: queue.Queue, interact_q: queue.Queue):
    print("  [Watch] 线程启动")
    sess = _make_session()
    last_gray = None
    _diag_suppressed = 0   # 仅诊断：本次触发前有多少拍"帧差超阈但没能投递"（OCR 忙/冷却中）
    panel_closed_since = None  # 面板首次被判为关的时刻，用于持续关迟滞
    roi = det.cfg.roi_chat
    # 节拍实测（修 P1-7）：设计值 WATCH_INTERVAL=0.1s，但真机反推只有 ~1.05s/拍，
    # 而所有"按拍计数"的安全参数（硬门防抖、邀请确认帧、消失宽限）都按 0.4s/拍算，
    # 实际被拉长 2.6 倍。这里把真实节拍打出来，让偏差可见、可量化。
    _cadence_t0 = time.time()
    _cadence_n = 0
    _cadence_print_at = time.time() + WATCH_CADENCE_LOG_EVERY
    _frame_cache = [None]  # 本拍帧缓存：一拍内只拷一次 6.2MB 全帧（原每拍最多拷 3 次）

    def _loop_frame():
        if _frame_cache[0] is None:
            _frame_cache[0] = det.latest_frame()
        return _frame_cache[0]

    while not shutdown_event.is_set():
        t0 = time.time()
        _frame_cache[0] = None
        _cadence_n += 1
        if t0 >= _cadence_print_at:
            _elapsed = t0 - _cadence_t0
            if _elapsed > 0 and _cadence_n > 1:
                print("  [Watch] 节拍实测: %.2f 拍/秒（%.0fms/拍，设计值 %.0fms）"
                      % (_cadence_n / _elapsed, _elapsed / _cadence_n * 1000.0,
                         WATCH_INTERVAL * 1000.0))
            _cadence_t0 = t0
            _cadence_n = 0
            _cadence_print_at = t0 + WATCH_CADENCE_LOG_EVERY
        try:
            snap = det.world.snapshot()

            if snap.screen == Screen.LOADING:
                last_gray = None
                time.sleep(WATCH_INTERVAL)
                continue

            with state.lock:
                busy = state.action_busy
                last_confirm = state.last_confirm_time
            if ACCEPT_TELEPORT_ENABLED and snap.confirm_dialog.value and not busy \
                    and not _confirm_in_progress \
                    and not _sending_msg \
                    and t0 - last_confirm > CONFIRM_COOLDOWN:
                with state.lock:
                    state.last_confirm_time = t0
                try:
                    action_q.put_nowait(("confirm",))
                except queue.Full:
                    pass

            # YOLO 检测牵手图标和聊天输入框（每隔 YOLO_DETECT_INTERVAL 秒检测一次）
            global _yolo_last_detect_time, _yolo_hand_detected, _yolo_hand_conf, _yolo_hand_bbox
            global _yolo_chat_input_detected, _yolo_chat_input_conf, _yolo_chat_input_bbox
            global _yolo_open_tree_detected, _yolo_open_tree_conf, _yolo_accept
            global _yolo_preview_boxes, _yolo_preview_time
            global _yolo_wing_light_seen, _yolo_wing_light_frames, _yolo_wing_light_last_seen
            if _yolo_model is not None and t0 - _yolo_last_detect_time > YOLO_DETECT_INTERVAL:
                _yolo_last_detect_time = t0
                frame = _loop_frame()   # 本拍共用一帧（原每拍最多拷 3 次 6.2MB 全帧）
                if frame is not None:
                    try:
                        results = _yolo_model(frame, conf=YOLO_CONF_THRESHOLD, verbose=False)
                        found_hand = False
                        found_open_tree = False
                        found_chat_input = False
                        found_wing_light = False
                        max_hand_conf = 0.0
                        max_open_tree_conf = 0.0
                        max_chat_input_conf = 0.0
                        max_wing_conf = 0.0
                        best_hand_bbox = None
                        best_chat_input_bbox = None
                        found_accept = {}  # action_name -> (conf, bbox)，取最高 conf
                        preview_boxes = []  # 全部框，供预览窗口
                        if len(results[0].boxes) > 0:
                            for box in results[0].boxes:
                                cls = int(box.cls[0])
                                conf = float(box.conf[0])
                                name = results[0].names[cls]
                                x1, y1, x2, y2 = map(float, box.xyxy[0])
                                preview_boxes.append((cls, conf, (x1, y1, x2, y2), name))
                                if name == "hand_icon":
                                    found_hand = True
                                    if conf > max_hand_conf:
                                        max_hand_conf = conf
                                        best_hand_bbox = (x1, y1, x2, y2)
                                elif name == "open_tree_icon":
                                    found_open_tree = True
                                    max_open_tree_conf = max(max_open_tree_conf, conf)
                                elif name == WING_LIGHT_CLASS_NAME and conf >= WING_LIGHT_CONF:
                                    # 光翼拾取图标（无翼绝不能按 F）：本拍标记，防抖/迟滞在检测后统一处理
                                    found_wing_light = True
                                    max_wing_conf = max(max_wing_conf, conf)
                                elif cls in ACCEPT_CLASSES:
                                    aname = ACCEPT_CLASSES[cls][0]
                                    if aname not in found_accept or conf > found_accept[aname][0]:
                                        found_accept[aname] = (conf, (x1, y1, x2, y2))
                                elif name == "chat_input":
                                    # 位置约束：根据实测 chat_input 在 (0,712,728,818) 附近
                                    fh, fw = frame.shape[:2]
                                    # 输入框贴屏幕底边(y2≈H)：上限放到 1.02H（2026-09-03 换显卡后
                                    # 实机标定，原 0.90H 把贴底输入框误杀，导致一直刷“位置不符”）
                                    if (x1 < fw * 0.10 and y1 > fh * 0.55
                                            and x2 < fw * 0.50 and y2 <= fh * 1.02):
                                        found_chat_input = True
                                        if conf > max_chat_input_conf:
                                            max_chat_input_conf = conf
                                            best_chat_input_bbox = (x1, y1, x2, y2)
                                    elif DEBUG:
                                        print(f"  [YOLO] chat_input 位置不符，忽略: ({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}) conf={conf:.2f}")
                        _yolo_hand_detected = found_hand
                        _yolo_hand_conf = max_hand_conf
                        _yolo_hand_bbox = best_hand_bbox
                        _yolo_chat_input_detected = found_chat_input
                        _yolo_chat_input_conf = max_chat_input_conf
                        _yolo_chat_input_bbox = best_chat_input_bbox
                        _yolo_open_tree_detected = found_open_tree
                        _yolo_open_tree_conf = max_open_tree_conf
                        _yolo_accept = found_accept
                        _yolo_preview_boxes = preview_boxes
                        _yolo_preview_time = t0
                        # wing_light 光翼硬门：纯函数状态机推进一拍（防抖进入、迟滞退出）
                        _prev_seen = _yolo_wing_light_seen
                        _yolo_wing_light_seen, _yolo_wing_light_frames, _yolo_wing_light_last_seen = \
                            wing_light_step(found_wing_light, t0, _yolo_wing_light_seen,
                                            _yolo_wing_light_frames, _yolo_wing_light_last_seen)
                        if _yolo_wing_light_seen and not _prev_seen:
                            print("  [Guard] 附近检出光翼，进入防护：拦截 F（无翼保护，不拾取）")
                        elif _prev_seen and not _yolo_wing_light_seen:
                            print("  [Guard] 已离开光翼范围，解除 F 拦截")
                        # 好友互动邀请事件（防抖/去重/OCR/VLM 观测；约0.4s一次）
                        _handle_accept_events(det, state, frame, t0, interact_q, action_q)
                        if found_hand and DEBUG:
                            print(f"  [YOLO] 检测到牵手图标 置信度={max_hand_conf:.2f} 位置={best_hand_bbox}")
                        if found_open_tree and DEBUG:
                            print(f"  [YOLO] 检测到打开好友树图标 置信度={max_open_tree_conf:.2f}（watch不按F，交给好友树动作）")
                        if found_chat_input and DEBUG:
                            print(f"  [YOLO] 检测到聊天输入框 置信度={max_chat_input_conf:.2f}")
                    except Exception as e:
                        if DEBUG:
                            print(f"  [YOLO] 检测异常: {e}")

            # 检测牵手图标：模板匹配（可选）或 YOLO
            template_hand = TEMPLATE_HAND_ENABLED and snap.f_prompt.value and snap.f_prompt_name.startswith("f_hand")
            hand_detected = template_hand or _yolo_hand_detected
            if hand_detected and not _sending_msg and not _friend_tree_busy:
                # 如果好友树打开了，先按 ESC 退出，不要按 F
                if snap.friend_tree.value:
                    print("  [Watch] 好友树开着，按 ESC 退出")
                    _key(sess, "escape", 120)
                    time.sleep(0.5)
                    continue
                with state.lock:
                    last_fhand = state.last_fhand_time
                    already_holding = state.is_holding_hands
                # 如果已经在牵手状态，不再按F（避免重复）
                if already_holding:
                    if DEBUG and t0 - last_fhand > 10.0:
                        print("  [Watch] 已在牵手状态，跳过按F")
                elif t0 - last_fhand > 8.0:
                    # 名字 OCR 确认：只对白名单玩家按 F（避免误牵其他人）
                    should_press_f = True
                    name_ocr_text = ""
                    if NAME_OCR_ENABLED and _yolo_hand_detected and _yolo_hand_bbox is not None:
                        try:
                            name_frame = _loop_frame()
                            name_ocr_text = ocr_player_name_above_icon(name_frame, _yolo_hand_bbox)
                            if name_ocr_text:
                                if is_whitelist_player_name(name_ocr_text):
                                    print(f"  [NameOCR] 识别到白名单玩家: '{name_ocr_text}'，允许牵手")
                                else:
                                    print(f"  [NameOCR] 识别到非白名单玩家: '{name_ocr_text}'，跳过牵手")
                                    should_press_f = False
                            else:
                                print(f"  [NameOCR] 未识别到名字，默认允许牵手（安全降级）")
                        except Exception as e:
                            print(f"  [NameOCR] 识别异常: {e}，默认允许牵手")
                    
                    if not should_press_f:
                        # 不按 F，但重置检测状态，避免重复判断
                        _yolo_hand_detected = False
                        continue
                    
                    with state.lock:
                        state.last_fhand_time = t0
                        state.is_holding_hands = True
                        state.hold_hands_start_time = t0
                        # 牵手后角色会被拉起来，姿势重置为站着
                        if state.pose_state != 'standing':
                            print(f"  [Watch] 牵手，姿势从 {state.pose_state} 重置为 standing")
                            state.pose_state = 'standing'
                            state.pose_update_time = t0
                    # 直接在 Watch 线程处理牵手，不等 Action 线程（图标出现时间很短）
                    detect_source = "YOLO" if _yolo_hand_detected and not (snap.f_prompt.value and snap.f_prompt_name.startswith("f_hand")) else "模板匹配"
                    print(f"  [Watch] 伸手了({detect_source})，按 F 牵住 → 进入牵手状态")
                    # 重置YOLO检测状态，避免重复按F
                    _yolo_hand_detected = False
                    # 光翼安全窗：牵手也是对着手伸图标按 F，与好友树入口同源风险
                    _f_ok, _f_why = f_prompt_press_allowed()
                    if not _f_ok:
                        print(f"  [Guard] 拒绝按 F 牵手：{_f_why}（本次不牵，8 秒后再看）")
                    else:
                        try:
                            if panel_open(det) is True:
                                _key(sess, "c", 150)
                                time.sleep(0.25)
                            _key(sess, "f", 120)
                            # 只按一次，不连续按（避免打开好友树）
                            time.sleep(1.0)
                            # 牵手前按 C 关了面板，牵住后主动开回来（守门在 HOME 兜底受限，见 _open_panel）
                            time.sleep(0.4)
                            if panel_open(det) is not True:
                                _key(sess, "c", 220)
                                if not wait_panel(det, True, 2.0) and panel_open(det) is not True:
                                    time.sleep(0.5)
                                    _key(sess, "c", 220)
                        except Exception as e:
                            print(f"  [Watch] 牵手异常: {e}")

            # 牵手状态超时重置（防止状态卡住）
            with state.lock:
                if state.is_holding_hands and t0 - state.hold_hands_start_time > state.hold_hands_timeout:
                    state.is_holding_hands = False
                    print("  [Watch] 牵手状态超时重置")

            with state.lock:
                last_ocr = state.last_chat_ocr_time
            if panel_open(det) is True and not MEM_READER_ENABLED:
                frame = _loop_frame()
                need = False
                _diff = -1.0   # 仅诊断：本拍帧差；-1 表示没有可比上一帧（首帧/尺寸变化）
                if frame is not None:
                    h, w = frame.shape[:2]
                    g = cv2.cvtColor(
                        frame[int(h*roi[1]):int(h*roi[3]),
                              int(w*roi[0]):int(w*roi[2])],
                        cv2.COLOR_BGR2GRAY)
                    g = cv2.GaussianBlur(g, (5, 5), 0)
                    if last_gray is None or g.shape != last_gray.shape:
                        need = True
                    else:
                        d = float(np.abs(g.astype(float)
                                         - last_gray.astype(float)).mean())
                        _diff = d
                        need = d > 4.5
                    last_gray = g
                if ocr_q.empty() and ((need and t0 - last_ocr > OCR_COOLDOWN) \
                        or t0 - last_ocr > OCR_FALLBACK):
                    if OCR_DIAG:
                        _why = "兜底" if not need else "帧差"
                        print(f"  [OCRDiag] 触发 原因={_why} diff={_diff:.1f} "
                              f"距上次={t0 - last_ocr:.1f}s 抑制={_diag_suppressed}")
                    _diag_suppressed = 0
                    try:
                        ocr_q.put_nowait(("chat",))
                    except queue.Full:
                        pass
                elif need:
                    _diag_suppressed += 1
            else:
                last_gray = None
                # 持续关迟滞：必须连续 PANEL_REOPEN_DELAY 都判关才认为真关；
                # 瞬时漏检/抖动期间保持 None 不按C，避免把其实开着的面板按关
                pstate = panel_open(det)
                if (snap.screen in (Screen.IN_WORLD, Screen.HOME)
                        and pstate is False
                        and not snap.confirm_dialog.value
                        and not snap.friend_tree.value):
                    if panel_closed_since is None:
                        panel_closed_since = t0
                else:
                    panel_closed_since = None
                with state.lock:
                    last_reopen = state.last_reopen_time
                    last_action = state.last_action_end
                if _guard_should_reopen(
                        snap, pstate, panel_closed_since, t0,
                        busy, _sending_msg, action_q.empty(),
                        last_action, last_reopen):
                    panel_closed_since = None
                    with state.lock:
                        state.last_reopen_time = t0
                    try:
                        action_q.put_nowait(("reopen",))
                    except queue.Full:
                        pass

        except Exception as e:
            print(f"  [Watch] 异常: {e}")

        time.sleep(max(0, WATCH_INTERVAL - (time.time() - t0)))

    print("  [Watch] 线程退出")

# ===================== OcrThread =====================

def ocr_loop(det: PanelDetector, state: SharedState,
             ocr_q: queue.Queue, ai_q: queue.Queue):
    print("  [OCR] 线程启动")
    ocr_engine = make_ocr_engine()
    voter = ChatVoteBuffer(OCR_VOTE_WINDOW) if OCR_VOTE_ENABLED else None

    while not shutdown_event.is_set():
        try:
            kind = ocr_q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            if kind[0] == "chat":
                _do_chat_ocr(det, state, ocr_engine, ai_q, voter)
        except Exception as e:
            print(f"  [OCR] 异常: {e}")

    print("  [OCR] 线程退出")


# ── 白名单过滤（使用者识别） ──

_WHITELIST_NAME_RE = re.compile(r'^\[([^\]]*)\]')


def _in_whitelist(name: str) -> bool:
    """检查发送者名字是否在白名单中。白名单关闭时始终返回True。"""
    if not WHITELIST_ENABLED:
        return True
    if not name:
        return False
    name = name.strip()
    for w in WHITELIST:
        if name == w or name.startswith(w) or w.startswith(name):
            return True
    return False


def _sender_name(msg: str) -> str:
    """从 '[名字] 内容' 格式的消息里取发送者名字（取不到返回空串）。"""
    m = _WHITELIST_NAME_RE.match(msg or "")
    return m.group(1).strip() if m else ""


# 记忆写入把关的可见化（2026-10-19 修第六十三章 P1-1）：
# 非白名单消息不写长期记忆是**设计行为**，但若白名单配置漂移、或 OCR 出的名字变体匹配不上，
# 星河的记忆会"静默停止更新"且完全无感。这里低频打印被跳过的发送者，让问题一眼可见。
_MEM_SKIP_COUNT = 0


def _log_memory_skip(name: str) -> None:
    """被跳过的记忆写入计数 + 低频告警（第 1 条必打，之后每 10 条打一次）。"""
    global _MEM_SKIP_COUNT
    _MEM_SKIP_COUNT += 1
    if _MEM_SKIP_COUNT == 1 or _MEM_SKIP_COUNT % 10 == 0:
        who = (name or "").strip() or "未知(未解析到名字)"
        print(f"  [Memory] 已跳过 {_MEM_SKIP_COUNT} 条非白名单消息不写入长期记忆"
              f"（最近发送者={who}；若这是珂珂本人，说明白名单配置或名字识别有问题）")


def _filter_by_whitelist(msgs: list[str]) -> list[str]:
    """过滤消息列表，只保留白名单玩家和AI自己的消息。
    白名单关闭时原样返回。"""
    if not WHITELIST_ENABLED:
        return msgs
    filtered = []
    dropped = 0
    for m in msgs:
        if _is_self_msg(m):
            filtered.append(m)  # 保留自己的消息用于认领防自循环
            continue
        match = _WHITELIST_NAME_RE.match(m)
        if match and _in_whitelist(match.group(1)):
            filtered.append(m)
        else:
            dropped += 1
    if dropped > 0 and DEBUG:
        print(f"  [白名单] 过滤掉 {dropped} 条非白名单消息")
    return filtered


def _latest_whitelist_msg(msgs: list[str]) -> str:
    """取最新的白名单玩家消息（不含自己的消息）。"""
    for m in reversed(msgs):
        if _is_self_msg(m):
            continue
        match = _WHITELIST_NAME_RE.match(m)
        if match and _in_whitelist(match.group(1)):
            return m
    return ""


# ── 牵手图标上方名字 OCR 确认 ──

_name_ocr_engine = None

def _get_name_ocr_engine():
    global _name_ocr_engine
    if _name_ocr_engine is None:
        _name_ocr_engine = make_ocr_engine()
    return _name_ocr_engine


def ocr_player_name_above_icon(frame, bbox):
    """识别牵手图标上方的玩家名字，返回识别到的名字文本。"""
    if frame is None or bbox is None:
        return ""
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    # 计算图标上方区域
    crop_y1 = max(0, int(y1) - NAME_OCR_ABOVE_PIXELS)
    crop_y2 = int(y1)
    crop_x1 = max(0, int(x1) - NAME_OCR_SIDE_PIXELS)
    crop_x2 = min(w, int(x2) + NAME_OCR_SIDE_PIXELS)
    if crop_y2 <= crop_y1 or crop_x2 <= crop_x1:
        return ""
    # 裁剪
    crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
    # 放大
    crop = cv2.resize(crop, None, fx=NAME_OCR_SCALE, fy=NAME_OCR_SCALE,
                       interpolation=cv2.INTER_CUBIC)
    # 灰度 + 对比度增强
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)
    # OCR 识别
    engine = _get_name_ocr_engine()
    result, _ = ocr_infer(engine, gray_eq)
    if not result:
        return ""
    texts = [str(r[1]).strip() for r in result if str(r[1]).strip()]
    return " ".join(texts)


def is_whitelist_player_name(text):
    """检查文本中是否包含白名单玩家的名字。"""
    if not text:
        return False
    for name in WHITELIST:
        if name and name in text:
            return True
    return False


def _do_chat_ocr(det, state, ocr_engine, ai_q, voter=None):
    global _ocr_diag_last_full
    frame = det.latest_frame()
    if frame is None:
        return
    _t_o = time.time()
    items = read_chat_ocr(frame, ocr_engine, det.cfg.roi_chat)
    if DEBUG:
        print(f'  [OCR] 推理耗时 {time.time()-_t_o:.1f}s / {len(items)}行')
    if voter is not None and items:
        items = voter.stabilize(items, time.time())

    with state.lock:
        state.last_chat_ocr_time = time.time()

    text = ocr_str(items)
    if DEBUG and text:
        print(f"  [OCR] {text.replace(chr(10), ' | ')[:160]}")
    if OCR_DIAG:
        # P0 插桩：用【完整文本】判断是否真的变了（上面日志那条是 [:160] 截断的，
        # 新消息追加在底部，前 160 字符可能完全不变，不能当签名）；底部 1~2 行原文
        # 用于人工分辨"真新消息"还是"同一段历史的重复识别"。
        _full = text or ""
        _changed = 0 if _full == _ocr_diag_last_full else 1
        _bottom = [ln.strip() for ln in _full.splitlines() if ln.strip()][-2:]
        print(f"  [OCRDiag] 行数={len(items)} 全文变={_changed} 底部=" + " | ".join(_bottom))
        _ocr_diag_last_full = _full

    now = time.time()
    with state.lock:
        state.sent_history = [(t, ts) for t, ts in state.sent_history
                              if now - ts < 600]
        own_texts = [t for t, ts in state.sent_history]
    msgs = extract(text, own_texts)
    if not msgs:
        return

    # 白名单过滤：只保留白名单玩家和AI自己的消息
    msgs = _filter_by_whitelist(msgs)
    if not msgs:
        return

    # 检测玩家说姿势没到位：把 pose_state 修正为玩家暗示的"实际姿势"，
    # 这样 AI 重新输出动作时，Action 能算出正确的补按次数。
    # 关键：必须是第二人称反馈（"你还坐着/你怎么还躺着"），要排除
    # "我们还是坐着吧"这种指令/提议（主语"我们"、句尾"吧"），否则会把
    # 指令误判成"实际已经是该姿势"，导致后续动作 press_count=0 被跳过。
    all_text = " ".join(msgs)
    # "我们…吧"是提议，不当反馈
    is_suggestion = ('我们' in all_text and '吧' in all_text)
    # 玩家描述实际姿势（第二人称"你还X着"）
    actual_hint = None
    second_person_patterns = [
        ('sitting', ['你还坐着', '你还是坐着', '你怎么还坐着', '你只是坐着', '你只坐着']),
        ('crouching', ['你还蹲着', '你还是蹲着', '你怎么还蹲着']),
        ('standing', ['你还站着', '你还是站着', '你怎么还站着']),
        ('lying', ['你还躺着', '你还是躺着', '你怎么还躺着']),
    ]
    if not is_suggestion:
        for pose, words in second_person_patterns:
            if any(w in all_text for w in words):
                actual_hint = pose
                break
    # 明确的"没成功"词（没说实际姿势，保守重置为 standing）
    explicit_fail = False
    if not is_suggestion:
        explicit_fail = any(w in all_text for w in [
            '没坐下', '没蹲下', '没躺下', '没躺上', '没坐上',
            '没有坐下', '没有蹲下', '没有躺下',
            '你没坐下', '你没蹲下', '你没躺下', '怎么还站着'])
    with state.lock:
        last_target = state.pose_last_target
        if actual_hint is not None:
            # 目标与实际不一致就需要补按（前进/后退都算）：
            # 目标 lying 实际 sitting、或目标 sitting 实际 lying 都要补；
            # 目标 sitting 实际 sitting 说明已到位，不补。
            need = POSE_STATES.get(last_target, 0) != POSE_STATES.get(actual_hint, 0)
            if need:
                if state.pose_state != actual_hint:
                    print(f"  [OCR] 玩家说实际是{actual_hint}（目标{last_target}），修正姿势、待补按")
                    state.pose_state = actual_hint
                state.pose_need_retry = True
        elif explicit_fail:
            if state.pose_state != 'standing':
                print(f"  [OCR] 玩家说姿势没成功，重置为 standing")
                state.pose_state = 'standing'
            state.pose_need_retry = True

    with state.lock:
        cur_latest = ""
        for m in reversed(msgs):
            if not _is_self_msg(m):
                cur_latest = m
                break
        cur_whitelist = _latest_whitelist_msg(msgs)

        now = time.time()
        new_whitelist = bool(cur_whitelist) and not _seen_before(state, cur_whitelist, now)
        new_other = bool(cur_latest) and not _seen_before(state, cur_latest, now)
        for m in msgs:
            if not _seen_before(state, m, now):
                state.recent_seen.append((m, now))
        if not new_whitelist and not new_other:
            return
        priority = new_whitelist

    try:
        ai_q.put_nowait((msgs, priority))
    except queue.Full:
        try:
            ai_q.get_nowait()
        except queue.Empty:
            pass
        try:
            ai_q.put_nowait((msgs, priority))
        except queue.Full:
            # 2026-10-19 修第六十三章 P2-1：丢玩家消息不能静默，至少留下痕迹
            print(f"  [AI-Q] 队列仍满，丢弃本批消息: {(' | '.join(msgs[-2:]))[:80]}")

# ===================== AiThread（重写：集成记忆/搜索/风格/防重复/打磨） =====================

def ai_loop(det, state: SharedState, ai_q: queue.Queue,
            action_q: queue.Queue, llm_client: LLMClient,
            interact_q: queue.Queue):
    print("  [AI] 线程启动（sky-companion 聊天引擎）")

    while not shutdown_event.is_set():
        # Delta B1：优先处理好友互动邀请事件（先开口反应，不按 F），不与玩家消息抢
        try:
            interact_event = interact_q.get_nowait()
        except queue.Empty:
            interact_event = None
        if interact_event is not None:
            try:
                _handle_interact_event(det, state, action_q, llm_client, interact_event)
            except Exception as e:
                print(f"  [互动反应] 处理异常: {e}")
            continue

        try:
            msgs, priority = ai_q.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            # ── 1. 提取最新玩家消息（过滤自己的消息） ──
            player_msg = ""
            player_raw = ""
            for m in reversed(msgs):
                if not _is_self_msg(m):
                    player_raw = m
                    player_msg = _NAME_PREFIX_RE.sub("", m, count=1).strip()
                    break
            if not player_msg:
                continue
            # 记忆写入把关（2026-10-19）：只有白名单玩家（或白名单关闭）的话
            # 才进长期记忆，防止路人的话被整理器当成珂珂的事写进 profile。
            mem_recordable = _in_whitelist(_sender_name(player_raw))

            # ── 2. 联网搜索判断 ──
            search_context = ""
            if SEARCH_ENABLED and needs_web_search(player_msg):
                query = build_search_query(player_msg)
                cache_key = query.lower()
                cached = state.search_cache.get(cache_key)
                if cached and time.time() - cached[0] < SEARCH_CACHE_SECONDS:
                    search_context = cached[1]
                else:
                    print(f"  [Search] {query[:60]}")
                    _t_s = time.time()
                    results = search_web(query, max_results=3, timeout=SEARCH_TIMEOUT)
                    print(f"  [Search] 联网耗时 {time.time()-_t_s:.1f}s")
                    results = filter_search_results(player_msg, results)
                    if results:
                        search_context = format_results(results)
                        state.search_knowledge = add_search_knowledge(query, search_context)
                        print(f"  [Search] {len(results)} results")
                    else:
                        search_context = "搜索没有拿到可靠结果。"
                        print("  [Search] empty")
                    state.search_cache[cache_key] = (time.time(), search_context)
                    if len(state.search_cache) > 20:
                        old_keys = sorted(state.search_cache,
                                          key=lambda k: state.search_cache[k][0])[:5]
                        for old_key in old_keys:
                            state.search_cache.pop(old_key, None)

            # ── 3. 风格学习（仅第一次，人设含关键词时触发） ──
            style_context = ""
            if STYLE_LEARN_ENABLED and not state.style_checked:
                state.style_checked = True
                pkey = style_prompt_key(SYSTEM)
                cached_key = state.style_knowledge.get("prompt_key")
                cached_prompt = state.style_knowledge.get("style_prompt", "")
                if cached_key == pkey and cached_prompt:
                    style_context = cached_prompt
                else:
                    def _style_llm(prompt, temperature=0.35, max_tokens=260):
                        return llm_client.chat(prompt, temperature=temperature, max_tokens=max_tokens)
                    style_context = learn_style(
                        SYSTEM, search_web, _style_llm,
                        cached_key=pkey, cached_prompt=cached_prompt,
                    )
                    if style_context:
                        state.style_knowledge = save_style_knowledge({
                            "prompt_key": pkey,
                            "style_prompt": style_context,
                        })
                        print("  [Style] learned")

            # ── 4. 已学搜索知识（按当前消息相关性检索） ──
            learned_search = search_knowledge_prompt(state.search_knowledge, player_msg)

            # ── 5. 组装增强 system prompt ──
            system_with_memory = SYSTEM
            system_with_memory += "\n\n## 长期记忆\n" + memory_prompt(state.memory)
            if style_context:
                system_with_memory += "\n\n## 说话风格参考\n" + style_context
            # 珂珂真实说话习惯画像（从 raw_turns 统计，纯本地；2026-10-19 新增）
            player_style = player_style_profile(state.memory.get("raw_turns", []))
            if player_style:
                system_with_memory += "\n\n## 珂珂的说话习惯（贴合TA的节奏接话，不是模仿TA）\n" + player_style
            if learned_search:
                system_with_memory += "\n\n## 已学到的联网知识\n" + learned_search
            # ── 记忆 v4：事件日记（四层检索的注入侧） ──
            _diary_have = bool(diary_recent(1))
            if _diary_have:
                # ① 默认层：最近几条日记 → 时间感（零检索成本）
                _dr = diary_recent(DIARY_INJECT_RECENT)
                if _dr:
                    system_with_memory += ("\n\n## 我们的记忆日记（最近发生的事）\n"
                                           + "\n".join(f"- {d['time']} {d['event']}" for d in _dr))
                # ② 触发层：这条消息命中回忆触发词 → 本地检索旧日记
                if player_msg and any(w in player_msg for w in RECALL_TRIGGER_WORDS):
                    _dh = diary_recall(player_msg)
                    if _dh:
                        system_with_memory += ("\n\n## 相关往日记忆（珂珂提到了过去的事）\n"
                                               + "\n".join(f"- {i['time']} {i['event']}" for _, i in _dh))
                        print(f"  [Diary] 回忆触发词命中，注入 {len(_dh)} 条旧日记")
                # ④ 协议层：RECALL 暗号说明（日记非空才教它，免得空转）
                system_with_memory += (
                    "\n\n## 回忆协议\n你有一本事件日记，还有周记/月记/年记"
                    "（上面注入的就是节选）。\n"
                    "当珂珂的问题涉及过去的人、事、约定或某个时间段，而你现有记忆里"
                    "没有足够答案时，先在回复的最开头输出"
                    " [RECALL]关键词[/RECALL]（例：[RECALL]犬夜叉[/RECALL]），"
                    "系统会同时检索事件日记和周/月/年记，再让你基于记忆重新回答。\n"
                    "关键词取问题里最具体、最可能被记下来的词（人名、地点、事件、话题）；"
                    "能直接答就别用，只在真的需要回忆且现有记忆不够时才用，"
                    "平时正常聊天绝不使用。"
                )

            # ── 记忆 v5：分层记忆（周记常驻 + 周/月/年触发检索） ──
            _wr = weekly_recent(1)
            if _wr:
                w = _wr[0]
                system_with_memory += (
                    "\n\n## 周记（最近一周我们的日子，保持这周在干嘛的连续感）\n"
                    f"- {w.get('period', '')}（{w.get('start', '')}~{w.get('end', '')}）："
                    f"{w.get('text', '')}"
                )
            if player_msg:
                if any(x in player_msg for x in WEEKLY_RECALL_WORDS):
                    _sh = summary_recall(player_msg, levels=("weekly",))
                    if _sh:
                        system_with_memory += (
                            "\n\n## 相关周记（珂珂提到了某一周）\n"
                            + "\n".join(f"- {it.get('period', '')}：{it.get('text', '')}"
                                        for _, _, it in _sh))
                        print(f"  [Summary] 周记触发，注入 {len(_sh)} 篇")
                elif any(x in player_msg for x in MONTHLY_RECALL_WORDS):
                    _sh = summary_recall(player_msg, levels=("monthly", "weekly"))
                    if _sh:
                        system_with_memory += (
                            "\n\n## 相关月记（珂珂提到了某个月）\n"
                            + "\n".join(f"- {it.get('period', '')}：{it.get('text', '')}"
                                        for _, _, it in _sh))
                        print(f"  [Summary] 月记触发，注入 {len(_sh)} 篇")
                elif any(x in player_msg for x in YEARLY_RECALL_WORDS):
                    _sh = summary_recall(player_msg, levels=("yearly", "monthly"))
                    if _sh:
                        system_with_memory += (
                            "\n\n## 相关年记（珂珂提到了某一年）\n"
                            + "\n".join(f"- {it.get('period', '')}：{it.get('text', '')}"
                                        for _, _, it in _sh))
                        print(f"  [Summary] 年记触发，注入 {len(_sh)} 篇")

            # ── 本地知识库：游戏常识（纯本地、零风险） ──
            if knowledge is not None:
                try:
                    kb_snippets = knowledge.query(player_msg)
                    if kb_snippets:
                        system_with_memory += (
                            "\n\n## 游戏常识（与当前话题相关，可作为背景）\n"
                            + "\n".join(kb_snippets))
                    core_notes = knowledge.core_notes()
                    if core_notes:
                        system_with_memory += (
                            "\n\n## 操作约定（务必遵守）\n"
                            + "\n".join("· " + n for n in core_notes))
                except Exception as _kb_err:
                    print("  [Knowledge] 注入失败: %s" % _kb_err)

            # 牵手状态注入
            with state.lock:
                holding = state.is_holding_hands
                pose = state.pose_state
            if holding:
                system_with_memory += "\n\n## 当前状态\n你现在正和珂珂牵着手，聊天时可以自然地提到牵手、跟着TA走、一起看风景等。不要反复强调牵手，自然融入对话即可。"

            # 姿势状态注入
            pose_names = {'standing': '站着', 'crouching': '蹲着', 'sitting': '坐着', 'lying': '躺着'}
            pose_name = pose_names.get(pose, '站着')
            pose_action_names = {v: k for k, v in POSE_ACTIONS.items()}  # lying->躺下
            with state.lock:
                need_retry = state.pose_need_retry
                last_target = state.pose_last_target
            if need_retry and last_target:
                target_name = pose_names.get(last_target, last_target)
                target_act = pose_action_names.get(last_target, last_target)
                system_with_memory += (
                    f"\n\n## 姿势需要重试（重要，本次必须输出动作）\n你刚才想{target_name}但没到位，"
                    f"玩家反馈你现在实际是{pose_name}。本次回复**必须**重新输出 [ACT]{target_act}[/ACT] "
                    f"标签（程序会自动补按差额），不要只在嘴上说'再试一次'却不输出动作标签。")
            elif pose != 'standing':
                system_with_memory += f"\n\n## 当前姿势\n你现在正{pose_name}。聊天时可以自然地提到你的姿势。如果玩家说'你没有{pose_name[:-1]}诶''你还站着'之类的话，说明动作没成功，你应该重新执行对应的姿势动作。"

            # 回遇境完成状态注入
            with state.lock:
                gohome_done = state.gohome_completed
            if gohome_done:
                system_with_memory += "\n\n## 刚刚发生的事\n你刚刚成功执行了回遇境，现在已经在遇境了。请自然地提到这件事（比如'终于回来了''到遇境啦'），不要太刻意。"

            # 待处理邀请状态注入：图标持续在屏时，让 AI 知道邀请还在等待（玩家可能在催/聊这件事）
            with state.lock:
                pending_now = [v.get("action_cn") for k, v in state.pending_invites.items()]
            pending_now = [x for x in pending_now if x]
            if pending_now:
                _acts = "、".join(pending_now)
                system_with_memory += (
                    f"\n\n## 当前有一个待处理的互动邀请\n屏幕上【{_acts}】的邀请图标还在，"
                    "你之前已经对它反应过了（不要重复“怎么突然…”的惊讶）。玩家现在可能正在和你聊这件事、或催你；"
                    "自然地接上对话即可，比如同意就说“好，抱你”之类。你可以在心里决定接不接受，但现在不能自己按 F"
                    "（要等阶段3程序执行），这一步也不要输出任何动作标签。")

            # 视觉不再做“固定触发词预识别”：改为在第7步注册 look_at_screen 工具，
            # 由模型自己判断何时看画面（场景/姿势/前方物体/图标都能问）。

            # ── 6. 组装用户消息内容 ──
            content = "\n".join(msgs[-10:])
            if priority:
                content += f"\n\n（{AKI}刚说了新消息，优先回TA）"
            else:
                if WHITELIST_ENABLED:
                    content += "\n\n（上面没有白名单玩家的新消息。可以不理。）"
                else:
                    content += "\n\n（上面没有珂珂的新消息。路人说的话可以不理。）"

            if search_context and "搜索没有拿到可靠结果" not in search_context:
                content += "\n\n## 联网搜索结果\n" + search_context

            ts = time.strftime("%H:%M:%S")
            print(f"\n[{ts}] {' | '.join(msgs[-3:])[:80]}")

            # 2026-10-19 修第六十五章 P0：trim() 内部会同步调 LLM（超时 30s、最多重试 1 次），
            # 原来放在 state.lock 里执行 → 最坏约 60 秒全程持锁，watch/action/主线程全部阻塞
            # （表现为"星河突然发呆几十秒"且日志里没有任何原因）。
            # state.conversation 只有 ai_loop 写（本处 + 下方追加 assistant），无跨线程竞争，
            # 所以锁只用来做快照，LLM 压缩放到锁外执行 —— 不影响光翼硬门等任何安全逻辑。
            with state.lock:
                state.conversation.append({"role": "user", "content": content})
                conv_now = list(state.conversation)
            conv_now = trim(conv_now, llm_client)          # 锁外：可能耗时数十秒
            with state.lock:
                state.conversation = conv_now
                conv_snapshot = (
                    [{"role": "system", "content": system_with_memory}]
                    + list(state.conversation)
                )
            # 记忆 v4：对话窗口持久化（锁外保存，重启接着聊）
            _save_conversation(conv_now)

            if AI_TRACE_ENABLED:
                _ai_trace_write(
                    "\n" + "=" * 72 +
                    "\n[%s] 触发玩家消息: %s | 优先回TA=%s | 带联网结果=%s\n"
                    "---- 发给模型的完整 messages（共%d条；第0条=人格+记忆+状态注入） ----\n%s"
                    % (time.strftime("%H:%M:%S"), player_msg, bool(priority),
                       bool(search_context), len(conv_snapshot),
                       _fmt_messages_for_trace(conv_snapshot)))

            # ── 7. 调用 LLM（注册视觉工具，让模型自己决定何时看画面）──
            _vision_ok = (_vision is not None and _vision.available)
            LOOK_SCREEN_TOOL = [{
                "type": "function",
                "function": {
                    "name": "look_at_screen",
                    "description": "看一眼当前游戏画面。当玩家问你看到了什么、在做什么动作、"
                                   "前面有什么、这是哪里、有没有坐下/躺下/牵手，或你需要确认当前"
                                   "画面、场景、姿势、图标时，就调用这个工具。问场景时要开放，"
                                   "不要把答案限定在你猜的两个地图里（否则会认错）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "你想从画面里确认的具体问题，例如‘我们前面有什么’"
                                               "‘玩家现在是什么姿势’‘这是哪个地图’‘有没有牵手’"
                            }
                        },
                        "required": ["question"]
                    }
                }
            }]
            # 场景类问题统一用这个【开放、列全地图】的 prompt，避免模型把问题写成
            # “遇境还是云野”这种二选一预设、把正确答案（如霞谷）直接排除（2026-10-05 真机根因）
            SCENE_STD_QUESTION = (
                "这是游戏《光·遇》的画面。请客观判断当前最可能是哪个地图/场景，"
                "从这些里选但不限于：遇境、云野、雨林、霞谷、暮土、禁阁、圣岛、小王子季等，"
                "能具体到具体地点更好。"
                "区分特征：霞谷=冰雪覆盖、滑行赛道、圆梦村/竞技场、尖顶城堡；"
                "云野=绿色草地、蝴蝶、浮岛、圆顶神殿；雨林=下雨、高大树木、发光蘑菇；"
                "暮土=荒漠、冥龙、黑色废墟；禁阁=高塔、多层书阁；遇境=环形石门加中央星盘。"
                "用一两句话说明判断依据（地形、建筑、光线、冰雪、植被等）。只说结论和依据，"
                "不要被任何先入猜测影响，画面和猜测矛盾时以画面为准。"
            )
            _t_llm = time.time()
            work_messages = list(conv_snapshot)
            msg = llm_client.chat_raw(
                work_messages, temperature=0.7, max_tokens=300,
                tools=LOOK_SCREEN_TOOL if _vision_ok else None)
            _tool_rounds = 0
            while msg.get("tool_calls") and _tool_rounds < 2 and _vision_ok:
                work_messages.append({
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": msg["tool_calls"]})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {}) or {}
                    tname = fn.get("name", "")
                    tid = tc.get("id", "")
                    if tname == "look_at_screen":
                        try:
                            _args = json.loads(fn.get("arguments") or "{}")
                            _q = _args.get("question") or "描述一下当前画面"
                        except Exception:
                            _q = "描述一下当前画面"
                        _frame = det.latest_frame()
                        print(f"  [Vision] 模型主动看画面: {_q}")
                        if _frame is not None:
                            # 场景类问题改用标准开放 prompt，避免模型“二选一”预设带偏
                            if any(k in _q for k in
                                   ("哪里", "哪儿", "什么地方", "场景", "地图", "在哪", "地方")):
                                _use_q = SCENE_STD_QUESTION
                                print("  [Vision] 场景类问题，使用标准开放 prompt")
                            else:
                                _use_q = _q
                            _r = _vision.ask(_frame, _use_q, max_tokens=200, temperature=0.1)
                        else:
                            _r = "（截图失败，无法查看）"
                        work_messages.append({
                            "role": "tool", "tool_call_id": tid,
                            "content": _r or "（没看清画面）"})
                    else:
                        work_messages.append({
                            "role": "tool", "tool_call_id": tid,
                            "content": "（未知工具）"})
                _tool_rounds += 1
                msg = llm_client.chat_raw(
                    work_messages, temperature=0.7, max_tokens=300,
                    tools=LOOK_SCREEN_TOOL)
            reply = msg.get("content") or ""
            _llm_dt = time.time() - _t_llm
            print(f'  [AI] LLM耗时 {_llm_dt:.1f}s')

            reply, _used_rc = _apply_recall_protocol(
                reply, work_messages, state, llm_client, player_msg)
            if AI_TRACE_ENABLED:
                try:
                    _raw_dump = json.dumps(
                        getattr(llm_client, "last_raw_message", None),
                        ensure_ascii=False, indent=2, default=str)
                except Exception:
                    _raw_dump = repr(getattr(llm_client, "last_raw_message", None))
                _ai_trace_write(
                    "---- 模型原始返回（耗时%.1fs）；raw 里若有 reasoning_content 会在此看到 ----\n%s\n"
                    "---- content 全文 ----\n%s"
                    % (_llm_dt, _raw_dump, reply))

            # 清除回遇境完成标志（AI已经知道了，下次回复不需要再提）
            with state.lock:
                if state.gohome_completed:
                    state.gohome_completed = False

            with state.lock:
                state.conversation.append({"role": "assistant", "content": reply})
                _conv_for_save = list(state.conversation)
            _save_conversation(_conv_for_save)  # 记忆 v4：持久化（锁外）

            # ── 8. 回复处理（打磨/防重复/重写/兜底） ──
            if "[IDLE]" in reply:
                print("  [AI] (idle)")
                if AI_TRACE_ENABLED:
                    _ai_trace_write("---- 程序最终处理 ----\n判定=IDLE，本轮不回复（模型认为无需开口）")
                if mem_recordable:
                    state.memory = add_turn(state.memory, player_msg, "")
                    _maybe_update_memory(state, llm_client)
                else:
                    _log_memory_skip(_sender_name(player_raw))
                continue

            # 协议宽容兜底：模型完全裸输出纯文本（无任何协议标签）时自动补 [CHAT]，
            # 避免“判定要发却因缺标签被安全门控静默丢弃”（2026-09-08 实机不回消息根因）
            reply = ensure_chat_wrapped(reply)
            # 提取 CHAT 部分
            chat_match = re.search(r'\[CHAT\](.*?)\[/CHAT\]', reply, re.DOTALL)
            chat_text = chat_match.group(1).strip() if chat_match else ""

            # 8a/8b. 兜底（仅当模型没产出任何可发送文字时）：联网搜索答案 / 固定回复。
            # 旧代码把这两段写在 `if chat_text:` 内层再判 `not chat_text`，条件永假、
            # 兜底从未生效（2026-09-11 由 DeepSeek 修复）。
            if not chat_text:
                if needs_web_search(player_msg):
                    def _search_llm(prompt, temperature=0.35, max_tokens=90):
                        return llm_client.chat(prompt, temperature=temperature, max_tokens=max_tokens)
                    chat_text = answer_from_search(player_msg, search_context, _search_llm)
                if not chat_text and must_reply(player_msg):
                    chat_text = fallback_reply(player_msg, SYSTEM)

            if chat_text:
                # 8c. 打磨（去笑声口癖）
                laugh_count = recent_laugh_count(state.my_words)
                chat_text = polish_reply(chat_text, laugh_count)

                # 8d. 防重复检测 + 自动重写
                if chat_text and reply_too_similar(chat_text, state.my_words,
                                                    companion_replies(state.memory)):
                    print("  [AI] Repeat: rewrite")
                    def _rewrite_llm(prompt, temperature=0.55, max_tokens=70):
                        return llm_client.chat(prompt, temperature=temperature, max_tokens=max_tokens)
                    rewritten = rewrite_repetitive_reply(
                        player_msg, chat_text, state.my_words, _rewrite_llm)
                    rewritten = polish_reply(rewritten, laugh_count)
                    if rewritten and not reply_too_similar(rewritten, state.my_words):
                        chat_text = rewritten
                        reply = re.sub(r'\[CHAT\].*?\[/CHAT\]',
                                       f'[CHAT]{chat_text}[/CHAT]',
                                       reply, flags=re.DOTALL)
                    elif not must_reply(player_msg):
                        chat_text = ""
                        reply = "[IDLE]"

                # 8e. 记录AI回复（防重复用）
                if chat_text:
                    state.my_words.append(chat_text)
                    if len(state.my_words) > 8:
                        state.my_words.pop(0)

            # ── 9. 先把回复发出去（2026-10-19 顺序调整：原来 add_turn/记忆整理
            #    阻塞在 action_q.put 前面，整合要花 1~3 秒，玩家会感觉星河卡一下） ──
            if "[IDLE]" in reply:
                print("  [AI] (idle after processing)")
                if AI_TRACE_ENABLED:
                    _ai_trace_write("---- 程序最终处理 ----\n判定=IDLE（防重复/兜底后认为不必回复）")
                if mem_recordable:
                    state.memory = add_turn(state.memory, player_msg, "")
                    _maybe_update_memory(state, llm_client)
                else:
                    _log_memory_skip(_sender_name(player_raw))
                continue
            print(f"  [AI] -> {reply[:120]}")
            if AI_TRACE_ENABLED:
                _ai_trace_write(
                    "---- 程序最终处理 ----\n判定=发送\n最终发出CHAT=%s\n---- reply标签全文 ----\n%s"
                    % (chat_text, reply))
            action_q.put(("execute", reply))

            # ── 10. 发完再记入记忆 / 触发记忆整理（仅白名单玩家，
            #     防路人消息污染长期记忆；2026-10-19） ──
            if mem_recordable:
                state.memory = add_turn(state.memory, player_msg, chat_text)
                _maybe_update_memory(state, llm_client)
            else:
                _log_memory_skip(_sender_name(player_raw))

        except Exception as e:
            print(f"  [AI] 异常: {e}")

    print("  [AI] 线程退出")

# ===================== ActionThread =====================

def action_loop(det: PanelDetector, state: SharedState,
                action_q: queue.Queue):
    print("  [Action] 线程启动")
    sess = _make_session()

    while not shutdown_event.is_set():
        try:
            cmd = action_q.get(timeout=1.0)
        except queue.Empty:
            continue

        with state.lock:
            state.action_busy = True
        try:
            if cmd[0] == "execute":
                _execute_reply(det, state, sess, cmd[1])
            elif cmd[0] == "confirm":
                _confirm_action(det, state, sess)
            elif cmd[0] == "go_home":
                _go_home_action(det, state, sess)
            elif cmd[0] == "accept_f":
                print("  [Action] 伸手了，按 F 牵住")
                _close_panel(det, sess)
                _key(sess, "f", 120)
                time.sleep(0.8)
                snap = det.world.snapshot()
                if snap.f_prompt.value and snap.f_prompt_name.startswith("f_hand"):
                    print("  [Action] 图标还在，再按一次 F")
                    _key(sess, "f", 120)
            elif cmd[0] == "accept_invite":
                # 阶段3：拟人接受好友互动邀请。normal=先发台词后延迟2s；fallback=兜底不延迟。
                _, aname, cn, reason = cmd
                if reason == "normal":
                    time.sleep(ACCEPT_PRESS_DELAY)
                # 复查邀请图标还在（用户中途取消就不按）
                if not _accept_present.get(aname):
                    print(f"  [阶段3] {cn} 邀请已消失（可能被取消），不按 F")
                    _accept_verdict[aname] = None
                else:
                    ok, why = f_prompt_press_allowed()  # 过光翼安全窗
                    if not ok:
                        print(f"  [Guard] 拒绝按 F：{why} —— {cn} 接受中止，未按任何键")
                        _accept_verdict[aname] = None
                    else:
                        # 台词已发出（execute 已完成），按 F 前先关聊天面板（面板开着时
                        # F 可能被输入拦截；与牵手/好友树动作一致）
                        _close_panel(det, sess)
                        # 关面板后再复查一次图标（按 C 期间用户可能取消）
                        if not _accept_present.get(aname):
                            print(f"  [阶段3] {cn} 关面板后邀请消失，不按 F")
                            _accept_verdict[aname] = None
                        else:
                            print(f"  [阶段3] {cn} 按 F 接受")
                            _key(sess, "f", 120)
                            # 确认图标消失（最多等2s，YOLO 需要1-2拍更新）
                            gone = False
                            _deadline = time.time() + 2.0
                            while time.time() < _deadline and not shutdown_event.is_set():
                                if not _accept_present.get(aname):
                                    gone = True
                                    break
                                time.sleep(0.2)
                            if not gone:
                                print(f"  [阶段3] {cn} 图标还在，补按一次 F")
                                _key(sess, "f", 120)
                                time.sleep(0.8)
                            _accept_verdict[aname] = "done"
                            print(f"  [阶段3] {cn} 接受完成")
            elif cmd[0] == "reopen":
                try:
                    det.dump_evidence("reopen")
                    if _open_panel(det, sess, state):
                        print("  [Action] 面板已按开（守门）")
                except RuntimeError as e:
                    if "前台" not in str(e) and "foreground" not in str(e).lower():
                        raise
        except Exception as e:
            print(f"  [Action] 异常: {e}")
        finally:
            with state.lock:
                state.action_busy = False
                state.last_action_end = time.time()

    print("  [Action] 线程退出")

# ===================== 启动日志（Tee 到 logs\，只留最近 5 次，方便报错排查） =====================

LOG_KEEP = 5  # logs 目录只保留最近 5 次启动日志；SKY_LOG=0 可整体关闭


class _TeeStream:
    """同时写控制台（保持系统编码）和 UTF-8 日志文件，线程安全。"""

    def __init__(self, console, logf, lock):
        self._console = console
        self._logf = logf
        self._lock = lock

    def write(self, s):
        if not s:
            return 0
        with self._lock:
            try:
                self._console.write(s)
            except Exception:
                try:
                    enc = getattr(self._console, "encoding", None) or "utf-8"
                    self._console.write(s.encode(enc, "replace").decode(enc, "replace"))
                except Exception:
                    pass
            try:
                self._logf.write(s)
                self._logf.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        with self._lock:
            for t in (self._console, self._logf):
                try:
                    t.flush()
                except Exception:
                    pass

    def __getattr__(self, name):
        return getattr(self._console, name)


def setup_run_log():
    """把本次启动的全部 stdout/stderr 同时落到 logs/loop_时间.txt（UTF-8），
    并滚动只保留最近 LOG_KEEP 个；写日志失败绝不影响主程序。返回日志路径或 None。"""
    if os.environ.get("SKY_LOG", "1") == "0":
        return None
    try:
        import traceback, atexit, platform
        log_dir = os.environ.get("SKY_LOG_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir,
                            "loop_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
        logf = open(path, "w", encoding="utf-8", buffering=1)
        lock = threading.Lock()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _TeeStream(old_out, logf, lock)
        sys.stderr = _TeeStream(old_err, logf, lock)

        def _close():
            try:
                with lock:
                    logf.write("\n===== 进程退出 %s =====\n"
                               % time.strftime("%Y-%m-%d %H:%M:%S"))
                    logf.flush(); logf.close()
            except Exception:
                pass
        atexit.register(_close)

        def _ex_hook(etype, value, tb):
            try:
                with lock:
                    logf.write("\n!!! 主线程未捕获异常 !!!\n")
                    logf.write("".join(traceback.format_exception(etype, value, tb)))
                    logf.flush()
            except Exception:
                pass
            old_out.write("".join(traceback.format_exception(etype, value, tb)))
        sys.excepthook = _ex_hook

        try:
            def _thread_hook(args):
                try:
                    with lock:
                        logf.write("\n!!! 线程 %s 未捕获异常 !!!\n" % args.thread.name)
                        logf.write("".join(traceback.format_exception(
                            args.exc_type, args.exc_value, args.exc_traceback)))
                        logf.flush()
                except Exception:
                    pass
            threading.excepthook = _thread_hook
        except Exception:
            pass

        print("=" * 60)
        print("光遇 AI 伙伴 启动日志  " + time.strftime("%Y-%m-%d %H:%M:%S"))
        print("python %s | %s %s" % (
            platform.python_version(), platform.system(), platform.release()))
        print("cwd=" + os.getcwd())
        print("脚本=" + os.path.abspath(__file__))
        print("聊天输入=%s | LLM=%s/%s | 按键后端=%s" % (
            "内存读取" if MEM_READER_ENABLED else "OCR截图",
            LLM_PROVIDER, MODEL, BACKEND))
        print("白名单=%s %s | 联网搜索=%s" % (
            WHITELIST_ENABLED, WHITELIST, SEARCH_ENABLED))
        print("YOLO=%s | 名字OCR=%s | OCR跨帧投票=%s | 白掩码 VMIN=%s SMAX=%s" % (
            YOLO_ENABLED, NAME_OCR_ENABLED, OCR_VOTE_ENABLED,
            WHITE_MASK_VMIN, WHITE_MASK_SMAX))
        print("云端视觉VLM=%s 可用=%s 模型=%s | 姿势校验=%s" % (
            VISION_ENABLED, _vision.available, _vision.model, VISION_POSE_CHECK))
        print("互动邀请 accept=%s 阈值=%.2f 连续帧=%d 冷却=%.0fs（%s）" % (
            "自动按F" if ACCEPT_AUTO else "仅观测",
            ACCEPT_CONF, ACCEPT_CONFIRM_FRAMES, ACCEPT_COOLDOWN,
            "会自动接受" if ACCEPT_AUTO else "不按F，只打[互动邀请]日志"))
        print("YOLO预览窗口=%s（SKY_YOLO_PREVIEW=1 开启，可看识别框）" % (
            "开" if YOLO_PREVIEW else "关"))
        print("=" * 60)

        # 滚动：只保留最近 LOG_KEEP 个 loop_*.txt（按修改时间倒序）
        try:
            files = [os.path.join(log_dir, f) for f in os.listdir(log_dir)
                     if f.startswith("loop_") and f.endswith(".txt")]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for old in files[LOG_KEEP:]:
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass
        print("[Log] 本次日志: %s（logs 目录仅保留最近 %d 次启动）" % (path, LOG_KEEP))
        return path
    except Exception as e:
        try:
            print("[Log] 启动日志初始化失败，已跳过:", e)
        except Exception:
            pass
        return None


# ===================== AI 对话转储（SKY_AI_TRACE=1 开启，排查“喂了什么/回了什么”） =====================
AI_TRACE_KEEP = LOG_KEEP
_ai_trace_path = None
_ai_trace_lock = threading.Lock()


def _fmt_messages_for_trace(messages):
    out = []
    for i, m in enumerate(messages):
        out.append("[%02d][role=%s]\n%s" % (i, m.get("role", "?"), m.get("content", "")))
    return "\n".join(out)


def setup_ai_trace():
    """开启后把每次发给 LLM 的完整输入/原始返回/最终处理写到 logs/ai_trace_时间.txt，
    独立于主日志、只保留最近 AI_TRACE_KEEP 份；任何失败都不影响主程序。返回路径或 None。"""
    global _ai_trace_path
    if not AI_TRACE_ENABLED:
        return None
    try:
        log_dir = os.environ.get("SKY_LOG_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        olds = [os.path.join(log_dir, f) for f in os.listdir(log_dir)
                if f.startswith("ai_trace_") and f.endswith(".txt")]
        olds.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in olds[AI_TRACE_KEEP:]:
            try:
                os.remove(old)
            except Exception:
                pass
        _ai_trace_path = os.path.join(
            log_dir, "ai_trace_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
        _ai_trace_write("===== AI TRACE 启动 %s | %s/%s | 输入后端=%s =====" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), LLM_PROVIDER, MODEL, BACKEND))
        print("[AITrace] 已开启 AI 对话转储: %s（仅保留最近 %d 份）" % (
            _ai_trace_path, AI_TRACE_KEEP))
        return _ai_trace_path
    except Exception as e:
        try:
            print("[AITrace] 初始化失败，已跳过:", e)
        except Exception:
            pass
        return None


def _ai_trace_write(block):
    if not AI_TRACE_ENABLED or not _ai_trace_path:
        return
    try:
        with _ai_trace_lock:
            with open(_ai_trace_path, "a", encoding="utf-8") as f:
                f.write(block)
                if not block.endswith("\n"):
                    f.write("\n")
                f.flush()
    except Exception:
        pass


# ===================== 主函数 =====================

def main():
    setup_run_log()  # 启动即落 TXT 日志（logs/，保留最近5次）
    setup_ai_trace()  # SKY_AI_TRACE=1 时另开 AI 对话转储 logs/ai_trace_*.txt
    if not SYSTEM:
        print("缺 persona.txt（人设文件）：把 persona.example.txt 复制成"
              " persona.txt 放在脚本旁边，写上你的 TA 是谁")
        return

    # 初始化统一 LLM 客户端
    llm_client = LLMClient(
        provider=LLM_PROVIDER,
        api_key=API_KEY,
        base_url=DEFAULT_BASE_URL,
        model=MODEL,
    )
    print(f"光遇循环调度 v7.1 (sky-companion 聊天引擎 + PanelDetector)")
    print(f"  LLM: {LLM_PROVIDER} / {MODEL}")
    print(f"  搜索: {'开启' if SEARCH_ENABLED else '关闭'}")
    print(f"  聊天输入: {'内存读取' if MEM_READER_ENABLED else 'OCR截图'}")
    print("=" * 40)

    # 0. 分层记忆补压缩（启动时检查：已结束的周/月/年没总结就补；低频、游戏外）
    if SUMMARY_ON_STARTUP:
        try:
            due = due_periods()
            n_due = len(due["weekly"]) + len(due["monthly"]) + len(due["yearly"])
            if n_due:
                print(f"[Summary] 发现 {n_due} 个待补总结（周{len(due['weekly'])} "
                      f"月{len(due['monthly'])} 年{len(due['yearly'])}），开始压缩…")
                added = summarize_due_periods(llm_client, companion_name="星河")
                print(f"[Summary] 完成：周记+{added['weekly']} 月记+{added['monthly']} "
                      f"年记+{added['yearly']}")
            else:
                print("[Summary] 分层记忆无缺口")
        except Exception as e:
            print(f"[Summary] 启动压缩失败（不影响主流程）: {e}")

    # 1. 定位游戏窗口
    region = find_game_client_region(DetectorConfig.window_titles)
    if region:
        follow = True
        print(f"窗口(客户区): {region}")
    else:
        follow = False
        sess = _make_session()
        st = json.loads(mcp("status", session=sess))
        win = st.get("window")
        if not win:
            print("找不到光遇窗口，先开游戏")
            return
        region = {"left": win["left"], "top": win["top"],
                  "width": max(1, win["width"]), "height": max(1, win["height"])}
        print(f"窗口(MCP整窗，可能含标题栏): {region}")

    # 2. MCP 连通性
    try:
        st = json.loads(mcp("status"))
        print(f"MCP:  后端={st.get('input_backend')}")
    except Exception as e:
        print(f"MCP 连不上: {e}")
        print("先跑: python sky-mcp-server.py --http --port 9900 --token 1234")
        return

    # 3. 启动检测器
    cv2.setNumThreads(2)
    det = PanelDetector(region=region, follow_window=follow,
                        cfg=DetectorConfig(debug=DEBUG,
                                           sense_interval=0.15,
                                           ocr_full_interval=10.0))
    det.start()

    def on_change(name, old, new, world):
        ts = time.strftime("%H:%M:%S")
        extra = ""
        if name == "f_prompt" and new:
            extra = f" ({world.f_prompt_name})"
        print(f"  [World] {name}: {old} -> {new}{extra}")
    det.world.on_change = on_change

    # 3.5 加载 YOLO 模型（可选）
    global _yolo_model, _wing_light_guard_online
    if YOLO_ENABLED:
        try:
            from ultralytics import YOLO
            if os.path.exists(YOLO_MODEL_PATH):
                _yolo_model = YOLO(YOLO_MODEL_PATH)
                print(f"  [YOLO] 模型加载成功: {os.path.basename(YOLO_MODEL_PATH)}")
                print(f"  [YOLO] 置信度阈值: {YOLO_CONF_THRESHOLD}")
                # 光翼硬门可见性（修 P0-3）：模型里没有 wing_light 类 = 硬门根本不会亮。
                # 例如回退旧 7 类 sky_v4_7cls（无该类）时，此前是完全静默的。
                try:
                    _names = set((_yolo_model.names or {}).values())
                    _wing_light_guard_online = WING_LIGHT_CLASS_NAME in _names
                except Exception:
                    _wing_light_guard_online = False
                if not _wing_light_guard_online:
                    print("  [Guard] ⚠ 该模型没有光翼类（wing_light），光翼硬门实际未生效！"
                          f"（当前查找的类名='{WING_LIGHT_CLASS_NAME}'，"
                          "换模型后可用 SKY_WING_LIGHT_CLASS_NAME 对齐）")
            else:
                print(f"  [YOLO] 模型文件不存在: {YOLO_MODEL_PATH}")
                print("  [YOLO] 将使用模板匹配检测牵手图标")
                _yolo_model = None
        except ImportError:
            print("  [YOLO] ultralytics 未安装，使用模板匹配")
            _yolo_model = None
        except Exception as e:
            print(f"  [YOLO] 加载失败: {e}，使用模板匹配")
            _yolo_model = None

    # 3.6 光翼硬门状态可见性（修 P0-3）：以前硬门失效是**完全静默**的，回退旧 7 类模型
    # （无 wing_light 类）后用户会以为还在保护。这里启动必打一行，说清到底在不在。
    if not WING_LIGHT_GUARD_ENABLED:
        _guard_desc = "已手动关闭（SKY_WING_LIGHT_GUARD=0）"
    elif _wing_light_guard_online:
        _guard_desc = "生效"
    else:
        _guard_desc = "未生效（YOLO 不可用或模型无光翼类）"
    print("光翼硬门=%s | UI提示F安全窗=%.0fs（最近见过光翼则禁止对着F提示按）" % (
        _guard_desc, WING_LIGHT_F_SAFE_WINDOW))
    if WING_LIGHT_GUARD_ENABLED and not _wing_light_guard_online and not F_ALLOW_WITHOUT_GUARD:
        print("  [Guard] ⚠ 硬门未生效期间：好友树/牵手类动作不会按 F（fail-safe，宁可不抱）；"
              "确认环境安全可设 SKY_F_ALLOW_WITHOUT_GUARD=1 放行")
    # 阈值名不副实的坑（P1-1）：低于推理阈值的框根本不会出现在结果里，类内二次过滤形同虚设
    if WING_LIGHT_CONF < YOLO_CONF_THRESHOLD:
        print("  [Guard] 提示：SKY_WING_LIGHT_CONF=%.2f < SKY_YOLO_CONF=%.2f，"
              "实际生效阈值=后者（推理时就已滤掉低置信框）。想放宽光翼检出请调低 SKY_YOLO_CONF。"
              % (WING_LIGHT_CONF, YOLO_CONF_THRESHOLD))
    if ACCEPT_CONF < YOLO_CONF_THRESHOLD:
        print("  [互动邀请] 提示：SKY_ACCEPT_CONF=%.2f < SKY_YOLO_CONF=%.2f，同上被后者卡住。"
              % (ACCEPT_CONF, YOLO_CONF_THRESHOLD))

    state = SharedState()
    ocr_q = queue.Queue(maxsize=2)
    ai_q = queue.Queue(maxsize=1)
    action_q = queue.Queue(maxsize=8)
    interact_q = queue.Queue(maxsize=8)  # Delta B1：好友互动邀请事件（watch 投递，ai 优先处理）

    # 内存读取回调：把消息放入AI队列
    mem_reader = None
    if MEM_READER_ENABLED:
        from mem_reader import MemoryChatReader
        def _on_mem_message(formatted_msgs):
            """内存读取到新消息时的回调。"""
            now = time.time()
            with state.lock:
                # 去重
                new_msgs = []
                for m in formatted_msgs:
                    if not _seen_before(state, m, now):
                        state.recent_seen.append((m, now))
                        new_msgs.append(m)
                if not new_msgs:
                    return
                # 判断是否有白名单消息
                has_whitelist = bool(_latest_whitelist_msg(new_msgs))
            try:
                ai_q.put_nowait((new_msgs, has_whitelist))
                if DEBUG:
                    print(f"  [MemReader] 送入AI队列: {new_msgs}")
            except queue.Full:
                try:
                    ai_q.get_nowait()
                    ai_q.put_nowait((new_msgs, has_whitelist))
                except queue.Empty:
                    pass

        wl = WHITELIST if WHITELIST_ENABLED else None
        mem_reader = MemoryChatReader(_on_mem_message, whitelist=wl, ai_name=AKI)
        # 预注册已知的sender_id（珂珂的ID可能随游戏版本变化）
        from mem_reader import load_uuid_map, save_uuid_map
        uuid_map = load_uuid_map()
        known_ke_ids = [
            'd238d9a1-f21c-47e7-b57a-b0ff6d5537a5',
            '72682bfc-a667-4738-8a6d-7b5a3c658422',
        ]
        changed = False
        for sid in known_ke_ids:
            if sid not in uuid_map:
                uuid_map[sid] = '珂珂'
                changed = True
        if changed:
            save_uuid_map(uuid_map)
        mem_reader.uuid_map = uuid_map
        mem_reader.start()
        print("  [MemReader] 内存聊天读取已启用（跳过OCR聊天识别）")

    threads = [
        threading.Thread(target=watch_loop, args=(det, state, ocr_q, action_q, interact_q),
                         name="Watch", daemon=True),
        threading.Thread(target=ai_loop, args=(det, state, ai_q, action_q, llm_client, interact_q),
                         name="AI", daemon=True),
        threading.Thread(target=action_loop, args=(det, state, action_q),
                         name="Action", daemon=True),
    ]
    # 只有未启用内存读取时才启动OCR线程
    if not MEM_READER_ENABLED:
        threads.insert(1, threading.Thread(target=ocr_loop, args=(det, state, ocr_q, ai_q),
                         name="OCR", daemon=True))
    for t in threads:
        t.start()

    print("Ctrl+C 退出")
    print("=" * 40)

    preview_on = YOLO_PREVIEW
    if preview_on:
        print("  [YOLO预览] 实时窗口已开启：窗口内按 q 或点右上角 × 关闭预览（不影响主程序）")
    last_dbg = 0.0

    def _close_preview():
        try:
            cv2.destroyWindow(_PREVIEW_WIN)
        except Exception:
            pass

    try:
        while True:
            if preview_on:
                try:
                    if not _draw_yolo_preview(det, time.time()):
                        preview_on = False
                        _close_preview()
                        print("  [YOLO预览] 预览窗口已关闭，主程序继续运行")
                except Exception as e:
                    preview_on = False
                    _close_preview()
                    print("  [YOLO预览] 绘制异常(已关闭预览):", e)
            else:
                time.sleep(2.0)
            if DEBUG and time.time() - last_dbg >= 2.0:
                last_dbg = time.time()
                snap_d = det.world.snapshot()
                desc = snap_d.describe()
                # hint_bar 主裁分（实机验证三态判据的关键观测点）
                hint = snap_d.metrics.get("hint_bar")
                if hint is not None:
                    verdict = judge_panel_by_hint(hint)
                    desc += f" [hint={hint:.2f}/{verdict}]"
                # 添加 YOLO 检测结果
                if YOLO_ENABLED and _yolo_model is not None:
                    yolo_parts = []
                    if _yolo_chat_input_detected:
                        yolo_parts.append(f"chat_input={_yolo_chat_input_conf:.2f}")
                    if _yolo_hand_detected:
                        yolo_parts.append(f"hand={_yolo_hand_conf:.2f}")
                    if yolo_parts:
                        desc += " [YOLO: " + ", ".join(yolo_parts) + "]"
                    else:
                        desc += " [YOLO: 无检测]"
                print("  " + desc)
    except KeyboardInterrupt:
        print("\n退出中...")
        shutdown_event.set()
        if mem_reader:
            mem_reader.stop()
        for t in threads:
            t.join(timeout=3.0)
        det.stop()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        print("已退出")


if __name__ == "__main__":
    main()
