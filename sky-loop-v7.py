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
                            find_game_client_region, make_ocr_engine)

# ── 移植自 sky-companion 的核心模块 ──
from core.llm_client import LLMClient
from core.memory import (
    load_memory, add_turn, memory_prompt, needs_update, update_profile,
    companion_replies, memory_pending_turns, build_memory_update_prompt,
    clean_memory_summary, load_search_knowledge, add_search_knowledge,
    search_knowledge_prompt, load_style_knowledge, save_style_knowledge,
)
from core.web_search import search_web, format_results
from core.reply_engine import (
    needs_web_search, build_search_query, filter_search_results,
    polish_reply, reply_too_similar, rewrite_repetitive_reply,
    recent_laugh_count, fallback_reply, must_reply, answer_from_search,
)
from core.style_learner import learn_style, style_prompt_key
from core.vision_client import VisionClient, pose_press_delta

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
    DEFAULT_MODEL = "deepseek-v4-pro"
else:
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

MODEL         = os.environ.get("SKY_LLM_MODEL", DEFAULT_MODEL)
SUMMARY_MODEL = MODEL

# 记忆
MEMORY_UPDATE_MIN_PENDING = 6

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
OCR_COOLDOWN     = 1.5
OCR_FALLBACK     = 10.0
CONFIRM_COOLDOWN = 6.0
REOPEN_COOLDOWN  = 6.0
PANEL_REOPEN_DELAY = 1.2  # 面板必须持续判关这么久才按C打开，滤掉瞬时漏检/抖动，避免把开着的面板按关
POST_ACTION_GRACE = 8.0
GOHOME_COOLDOWN  = 45.0

MAX_MSGS        = 40
SUMMARY_TRIGGER = 30
SUMMARY_KEEP    = 15

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
YOLO_MODEL_PATH = os.environ.get("SKY_YOLO_MODEL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "runs", "detect", "runs", "detect", "train", "weights", "best.pt"))
YOLO_CONF_THRESHOLD = float(os.environ.get("SKY_YOLO_CONF", "0.8"))
YOLO_DETECT_INTERVAL = 0.4  # YOLO检测间隔（秒），1秒约2.5次

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

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.conversation: list[dict] = []
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

# ===================== 面板操作（读 WorldState） =====================

def panel_open(det: PanelDetector) -> bool | None:
    """判断聊天面板是否打开。三态主裁（2026-09-10 接入，159 帧真机标定）：
    1. hint_bar 底部提示行模板相关分明确时它说了算（开>=0.50 / 关<=0.20）——
       "面板开独有、关必无、位置固定"的锚点，治传统暗横条在关帧咬死 1.00 的死锁
       （续26 实机定性、续42 离线全量验证：开 0.55~0.84 / 关 0~0.44，空档清晰）；
    2. hint 分落在中间带（关闭动画渐隐/亮景火光/没放模板/本帧没算分）时，
       回退旧的 YOLO OR 传统像素融合。
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
    # hint 中间带/无模板：沿用旧双通道融合（YOLO + 传统，任一认为开就判开）
    traditional = snap.chat_open.value
    if YOLO_ENABLED and _yolo_model is not None:
        if _yolo_chat_input_detected or traditional:
            return True
        return False
    return traditional


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


def _key(sess, k, ms=80):
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


def _ensure_panel_open_for_watch(det, sess, timeout=5.0, need=2, gap=0.15,
                                 press_cooldown=1.8, max_press=2):
    """看门打开聊天面板：连续 need 次确认开才算成功。补按前提更严格——必须从未见过‘开’
    且连续 need 次明确关、距上次按C超 press_cooldown 才补下一次。一旦见过开，说明面板已
    打开，之后判关是检测漏检，绝不再按C（再按会把开着的面板关掉=肉眼看到的一闪）。"""
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
    if snap.screen != Screen.IN_WORLD or snap.confirm_dialog.value:
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


def _pose_area_has_other(frame):
    """姿势视觉校验前的本地安全闸：在角色裁剪区做一次轻量 OCR，若出现白名单其他
    玩家的名字标签，说明旁边站着人，VLM 可能把别人当成自己（实测会选成站着的他人）；
    此时调用方跳过视觉校验、回退盲信，绝不因认错人而乱补按。"""
    if frame is None or _vision is None:
        return False
    c = getattr(_vision, "pose_crop", None) or (0.20, 0.35, 0.90, 1.0)
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = int(w*c[0]), int(h*c[1]), int(w*c[2]), int(h*c[3])
    crop = frame[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    res, _ = _get_name_ocr_engine()(gray)
    text = " ".join(str(r[1]) for r in (res or []))
    if text.strip() and DEBUG:
        print("  [Vision] 角色区OCR文字:", text[:60])
    return is_whitelist_player_name(text)


def _verify_pose_with_vision(det, sess, target_pose, max_round=2, settle=1.4):
    """按完姿势键后用 VLM 看一眼实际姿势，没到目标则【每次只补1下】再看，最多 max_round 轮。
    关键保护：补按后若到目标的循环距离没有变小（VLM抖动/动画未落定），立即停止，
    绝不一补补好几下导致姿势绕一圈又站回去。VLM 不可用/看不清/异常时回退‘盲信目标’。"""
    def _observe():
        time.sleep(settle)  # 等姿势切换动画彻底落定，避免看到中间帧
        frame = det.latest_frame()
        try:
            if _pose_area_has_other(frame):
                print("  [Vision] 旁边检测到其他玩家，VLM 可能认错人，跳过本次校验回退")
                return None
        except Exception as _e:
            print(f"  [Vision] 他人检测异常(继续校验): {_e}")
        pose, sure = _vision.classify_pose(frame)
        print(f"  [Vision] 姿势校验: 目标={target_pose} 实测={pose} sure={sure}")
        return pose if sure else None
    observed = _observe()
    if observed == target_pose:
        return target_pose
    for _ in range(max_round):
        if observed is None or observed not in POSE_STATES:
            break
        dist = pose_press_delta(POSE_STATES[observed], POSE_STATES[target_pose])
        if dist == 0:
            break
        print(f"  [Vision] 实测{observed}未到{target_pose}（差{dist}），保守补按1次3")
        _key(sess, "3", 80)
        time.sleep(0.55)
        prev_dist, observed = dist, _observe()
        if observed in POSE_STATES and observed != target_pose:
            new_dist = pose_press_delta(POSE_STATES[observed], POSE_STATES[target_pose])
            if new_dist >= prev_dist:
                print("  [Vision] 补按后未更接近目标，停止补按以防绕圈站起")
                break
    return observed if observed in POSE_STATES else target_pose


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
                    if "前台" in str(e) and i < 5:
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
                if "前台" in str(e) and i < 5:
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
    # 策略：默认选中的是确认（✓），直接按空格，不按方向键
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


def _friend_tree_action(det, sess, act):
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
            _key(sess, "f", 120)

        # 首按前采一次"底噪"主动分（场景差异大：真机实测 0.08~0.57），供"相对增量"门限使用
        _sc_base, _ = det.friend_tree_score_now()
        if _sc_base is not None and _sc_base < 0:
            _sc_base = None
        _press_f()
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
                _press_f()
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

# 好友树互动动作：动作名 -> 打开好友树后的方向键导航序列（按动作名可配置，便于以后加击掌等）
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


def _execute_reply(det, state, sess, reply):
    # 先检查是否有回遇境动作，如果有就先执行动作，跳过消息发送
    has_gohome = '[ACT]回遇境[/ACT]' in reply
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

    for m in re.finditer(r'\[ACT\](.*?)\[/ACT\]', reply, re.DOTALL):
        act = m.group(1).strip()
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
            _friend_tree_action(det, sess, act)
            continue
        # 姿势动作：智能切换，根据当前状态计算需要按几次3
        if act in POSE_ACTIONS:
            target_pose = POSE_ACTIONS[act]
            with state.lock:
                current_pose = state.pose_state
            current_idx = POSE_STATES.get(current_pose, 0)
            target_idx = POSE_STATES.get(target_pose, 0)
            # 计算需要按几次3（4次一个循环：站->蹲->坐->躺->站）
            press_count = (target_idx - current_idx + 4) % 4
            if press_count == 0:
                print(f"  [Action] {act} -> 已经是{target_pose}，跳过")
                continue
            if not _close_panel(det, sess):
                print(f"  [Action] 面板可能还开着，{act} 是游戏键，照做")
            print(f"  [Action] {act} -> 当前{current_pose}，目标{target_pose}，按{press_count}次3")
            for i in range(press_count):
                _key(sess, "3", 80)
                time.sleep(0.55)
            final_pose = target_pose
            if VISION_POSE_CHECK and _vision.available:
                final_pose = _verify_pose_with_vision(det, sess, target_pose)
            with state.lock:
                state.pose_state = final_pose
                state.pose_update_time = time.time()
                state.pose_need_retry = False
                # 光遇中坐下/蹲下/躺下会自动断开牵手，同步重置牵手状态
                if state.is_holding_hands:
                    print(f"  [Action] 姿势动作{act}会断开牵手，重置牵手状态")
                    state.is_holding_hands = False
            print(f"  [Action] 姿势状态更新为: {final_pose}")
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
                    time.sleep(1.0)
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
    for name, im in variants:
        result, _ = ocr_engine(im)
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
        result = llm_client.chat(prompt, temperature=0.2, max_tokens=520)
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

def watch_loop(det: PanelDetector, state: SharedState,
               ocr_q: queue.Queue, action_q: queue.Queue):
    print("  [Watch] 线程启动")
    sess = _make_session()
    last_gray = None
    panel_closed_since = None  # 面板首次被判为关的时刻，用于持续关迟滞
    roi = det.cfg.roi_chat

    while not shutdown_event.is_set():
        t0 = time.time()
        try:
            snap = det.world.snapshot()

            if snap.screen == Screen.LOADING:
                last_gray = None
                time.sleep(WATCH_INTERVAL)
                continue

            with state.lock:
                busy = state.action_busy
                last_confirm = state.last_confirm_time
            if snap.confirm_dialog.value and not busy \
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
            global _yolo_open_tree_detected, _yolo_open_tree_conf
            if _yolo_model is not None and t0 - _yolo_last_detect_time > YOLO_DETECT_INTERVAL:
                _yolo_last_detect_time = t0
                frame = det.latest_frame()
                if frame is not None:
                    try:
                        results = _yolo_model(frame, conf=YOLO_CONF_THRESHOLD, verbose=False)
                        found_hand = False
                        found_open_tree = False
                        found_chat_input = False
                        max_hand_conf = 0.0
                        max_open_tree_conf = 0.0
                        max_chat_input_conf = 0.0
                        best_hand_bbox = None
                        best_chat_input_bbox = None
                        if len(results[0].boxes) > 0:
                            for box in results[0].boxes:
                                cls = int(box.cls[0])
                                conf = float(box.conf[0])
                                name = results[0].names[cls]
                                x1, y1, x2, y2 = map(float, box.xyxy[0])
                                if name == "hand_icon":
                                    found_hand = True
                                    if conf > max_hand_conf:
                                        max_hand_conf = conf
                                        best_hand_bbox = (x1, y1, x2, y2)
                                elif name == "open_tree_icon":
                                    found_open_tree = True
                                    max_open_tree_conf = max(max_open_tree_conf, conf)
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
                            name_frame = det.latest_frame()
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
                    try:
                        if panel_open(det) is True:
                            _key(sess, "c", 150)
                            time.sleep(0.25)
                        _key(sess, "f", 120)
                        # 只按一次，不连续按（避免打开好友树）
                        time.sleep(1.0)
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
                frame = det.latest_frame()
                need = False
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
                        need = d > 4.5
                    last_gray = g
                if (need and t0 - last_ocr > OCR_COOLDOWN) \
                        or t0 - last_ocr > OCR_FALLBACK:
                    try:
                        ocr_q.put_nowait(("chat",))
                    except queue.Full:
                        pass
            else:
                last_gray = None
                # 持续关迟滞：必须连续 PANEL_REOPEN_DELAY 都判关才认为真关；
                # 瞬时漏检/抖动期间保持 None 不按C，避免把其实开着的面板按关
                if (snap.screen == Screen.IN_WORLD
                        and panel_open(det) is False
                        and not snap.confirm_dialog.value):
                    if panel_closed_since is None:
                        panel_closed_since = t0
                else:
                    panel_closed_since = None
                with state.lock:
                    last_reopen = state.last_reopen_time
                    last_action = state.last_action_end
                if (snap.screen == Screen.IN_WORLD
                        and panel_closed_since is not None
                        and t0 - panel_closed_since > PANEL_REOPEN_DELAY
                        and not busy
                        and not _sending_msg
                        and action_q.empty()
                        and not MEM_READER_ENABLED
                        and t0 - last_action > POST_ACTION_GRACE
                        and t0 - last_reopen > REOPEN_COOLDOWN):
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
    result, _ = engine(gray_eq)
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

    # 检测玩家说动作没成功，重置姿势状态
    all_text = " ".join(msgs)
    pose_retry_keywords = {
        '坐下': ['没坐下', '你没有坐下', '你还站着', '怎么还站着', '没坐上', '你没坐下'],
        '蹲下': ['没蹲下', '你没有蹲下', '你没蹲下'],
        '躺下': ['没躺下', '你没有躺下', '你没躺下', '没躺上'],
    }
    for pose, keywords in pose_retry_keywords.items():
        if any(kw in all_text for kw in keywords):
            with state.lock:
                if state.pose_state != 'standing':
                    print(f"  [OCR] 玩家说{pose}没成功，重置姿势状态")
                    state.pose_state = 'standing'
                    state.pose_need_retry = True
            break

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
            pass

# ===================== AiThread（重写：集成记忆/搜索/风格/防重复/打磨） =====================

def ai_loop(state: SharedState, ai_q: queue.Queue,
            action_q: queue.Queue, llm_client: LLMClient):
    print("  [AI] 线程启动（sky-companion 聊天引擎）")

    while not shutdown_event.is_set():
        try:
            msgs, priority = ai_q.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            # ── 1. 提取最新玩家消息（过滤自己的消息） ──
            player_msg = ""
            for m in reversed(msgs):
                if not _is_self_msg(m):
                    player_msg = _NAME_PREFIX_RE.sub("", m, count=1).strip()
                    break
            if not player_msg:
                continue

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
            if learned_search:
                system_with_memory += "\n\n## 已学到的联网知识\n" + learned_search

            # 牵手状态注入
            with state.lock:
                holding = state.is_holding_hands
                pose = state.pose_state
            if holding:
                system_with_memory += "\n\n## 当前状态\n你现在正和珂珂牵着手，聊天时可以自然地提到牵手、跟着TA走、一起看风景等。不要反复强调牵手，自然融入对话即可。"

            # 姿势状态注入
            pose_names = {'standing': '站着', 'crouching': '蹲着', 'sitting': '坐着', 'lying': '躺着'}
            pose_name = pose_names.get(pose, '站着')
            if pose != 'standing':
                system_with_memory += f"\n\n## 当前姿势\n你现在正{pose_name}。聊天时可以自然地提到你的姿势。如果玩家说'你没有{pose_name[:-1]}诶''你还站着'之类的话，说明动作没成功，你应该重新执行对应的姿势动作。"

            # 回遇境完成状态注入
            with state.lock:
                gohome_done = state.gohome_completed
            if gohome_done:
                system_with_memory += "\n\n## 刚刚发生的事\n你刚刚成功执行了回遇境，现在已经在遇境了。请自然地提到这件事（比如'终于回来了''到遇境啦'），不要太刻意。"

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

            with state.lock:
                state.conversation.append({"role": "user", "content": content})
                state.conversation = trim(state.conversation, llm_client)
                conv_snapshot = (
                    [{"role": "system", "content": system_with_memory}]
                    + list(state.conversation)
                )

            # ── 7. 调用 LLM ──
            if AI_TRACE_ENABLED:
                _ai_trace_write(
                    "\n" + "=" * 72 +
                    "\n[%s] 触发玩家消息: %s | 优先回TA=%s | 带联网结果=%s\n"
                    "---- 发给模型的完整 messages（共%d条；第0条=人格+记忆+状态注入） ----\n%s"
                    % (time.strftime("%H:%M:%S"), player_msg, bool(priority),
                       bool(search_context), len(conv_snapshot),
                       _fmt_messages_for_trace(conv_snapshot)))
            _t_llm = time.time()
            reply = llm_client.chat(
                messages=conv_snapshot,
                temperature=0.7,
                max_tokens=300,
            )
            _llm_dt = time.time() - _t_llm
            print(f'  [AI] LLM耗时 {_llm_dt:.1f}s')
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

            # ── 8. 回复处理（打磨/防重复/重写/兜底） ──
            if "[IDLE]" in reply:
                print("  [AI] (idle)")
                if AI_TRACE_ENABLED:
                    _ai_trace_write("---- 程序最终处理 ----\n判定=IDLE，本轮不回复（模型认为无需开口）")
                state.memory = add_turn(state.memory, player_msg, "")
                _maybe_update_memory(state, llm_client)
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

            # ── 9. 记入记忆 ──
            state.memory = add_turn(state.memory, player_msg, chat_text)

            # ── 10. 触发记忆整理 ──
            _maybe_update_memory(state, llm_client)

            # ── 11. 放入 action_q ──
            if "[IDLE]" in reply:
                print("  [AI] (idle after processing)")
                if AI_TRACE_ENABLED:
                    _ai_trace_write("---- 程序最终处理 ----\n判定=IDLE（防重复/兜底后认为不必回复）")
                continue
            print(f"  [AI] -> {reply[:120]}")
            if AI_TRACE_ENABLED:
                _ai_trace_write(
                    "---- 程序最终处理 ----\n判定=发送\n最终发出CHAT=%s\n---- reply标签全文 ----\n%s"
                    % (chat_text, reply))
            action_q.put(("execute", reply))

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
            elif cmd[0] == "reopen":
                try:
                    det.dump_evidence("reopen")
                    if _open_panel(det, sess, state):
                        print("  [Action] 面板已按开（守门）")
                except RuntimeError as e:
                    if "前台" not in str(e):
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
    global _yolo_model
    if YOLO_ENABLED:
        try:
            from ultralytics import YOLO
            if os.path.exists(YOLO_MODEL_PATH):
                _yolo_model = YOLO(YOLO_MODEL_PATH)
                print(f"  [YOLO] 模型加载成功: {os.path.basename(YOLO_MODEL_PATH)}")
                print(f"  [YOLO] 置信度阈值: {YOLO_CONF_THRESHOLD}")
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

    state = SharedState()
    ocr_q = queue.Queue(maxsize=2)
    ai_q = queue.Queue(maxsize=1)
    action_q = queue.Queue(maxsize=8)

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
        threading.Thread(target=watch_loop, args=(det, state, ocr_q, action_q),
                         name="Watch", daemon=True),
        threading.Thread(target=ai_loop, args=(state, ai_q, action_q, llm_client),
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

    try:
        while True:
            time.sleep(2.0)
            if DEBUG:
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
        print("已退出")


if __name__ == "__main__":
    main()
