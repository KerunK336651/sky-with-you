# panel_detector.py — 光遇固定 UI 检测器 + WorldState v1.0
# -*- coding: utf-8 -*-
#
# 独立模块，不依赖 LLM。三层证据融合：
#   1. 视觉启发式（每帧，<1ms）：ROI 内 dark/edge/saturation 统计
#   2. 模板匹配（每帧，小 ROI）：templates/<channel>/*.png，cv2.matchTemplate
#   3. OCR（低频）：RapidOCR，弹窗关键词确认 / 场景兜底
#
# 检测目标：
#   chat         聊天面板（左下角消息区）
#   friend_tree  好友树 / 好友星盘（牵手/拥抱/解锁那个界面）
#   confirm      确认弹窗（传送/加好友/是否前往…）
#   f_prompt     F 交互提示（牵手/点火等）
#   screen       互斥场景：LOADING / HOME(遇境) / IN_WORLD / UNKNOWN
#
# 用法一（自采集，内部起截屏线程）:
#   det = PanelDetector(region={"left":0,"top":0,"width":1920,"height":1080})
#   det.start()
#   snap = det.world.snapshot()   # 决策层只读这个
#
# 用法二（外部喂帧，嵌进已有 Sense 线程）:
#   det = PanelDetector()
#   det.start_ocr()               # 只起 OCR 线程
#   ... 循环里: det.process_frame(frame)
#
# 外部 OCR 结果（如 MCP read_screen）也可以喂进来:
#   det.ingest_ocr([{"text": "...", "confidence": 0.9, "x": 0, "y": 0}, ...])
#
# 模板目录结构（可选，没有模板时视觉+OCR 仍然工作）:
#   templates/chat/*.png  templates/confirm/*.png  templates/f_prompt/*.png
#   templates/friend_tree/*.png  templates/home/*.png
#   模板按 ref_height=1080 截取，运行时按帧高自动缩放。
#
# 标定：python panel_detector.py --region L,T,W,H --dump  会把各 ROI 存成 png。

from __future__ import annotations

import os
# 与游戏同机运行：限住推理/图像库线程数，避免吃满CPU把游戏卡死
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

import sys
import glob
import time
import queue
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

import numpy as np
import cv2
try:
    cv2.setNumThreads(2)
except Exception:
    pass

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:
    RapidOCR = None

# OCR 推理设备：auto（有 DirectML 就用 GPU）/ dml（强制 GPU）/ cpu（强制 CPU）
# DirectML 走 DirectX 12，AMD/N卡/核显通用；实测 RX5600XT 上 OCR 提速约 3 倍，
# 能直接缓解多人场景 OCR 排队卡死。需安装 onnxruntime-directml（与 CPU 版互斥）。
OCR_DEVICE = os.environ.get("SKY_OCR_DEVICE", "auto").lower().strip()

# OCR 模型版本：v3（RapidOCR 自带，稳定）/ v5（PP-OCRv5，行切分更干净、
# 支持简繁/拼音/日文，模型在 models/ocr/，缺失时自动回退 v3）。
OCR_MODEL = os.environ.get("SKY_OCR_MODEL", "v3").lower().strip()
_OCR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "ocr")
_OCR_V5 = {
    "det": os.path.join(_OCR_DIR, "v5_det.onnx"),
    "rec": os.path.join(_OCR_DIR, "v5_rec.onnx"),
    "keys": os.path.join(_OCR_DIR, "ppocr_keys_v5.txt"),
}


def _patch_rapidocr_dml():
    """让 RapidOCR 的 onnxruntime session 优先走 DirectML；并让 rec_keys_path
    正确落到 config['keys_path']。返回是否启用了 DML。

    注意：补丁是打在 rapidocr_onnxruntime.utils.InferenceSession 上的进程级全局替换，
    **一旦打上，本进程内再改 OCR_DEVICE='cpu' 也不会退回 CPU**。正常情况下无影响——
    OCR_DEVICE 只在 import 时从环境变量读一次，运行中不会改。但手工做对比实验时
    必须"先测 cpu 再测 auto/dml"，否则会看到与预期相反的结果（test_ocr_engine.py
    就是按这个顺序写的）。"""
    dml_on = False
    try:
        import rapidocr_onnxruntime.utils as _u
        import onnxruntime as _ort

        # 1) DirectML（仅在 auto/dml 时启用；cpu 时强制走 CPU）
        if OCR_DEVICE in ("auto", "dml") \
                and "DmlExecutionProvider" in _ort.get_available_providers():
            _orig = _u.InferenceSession
            if not getattr(_orig, "_sky_dml_patched", False):
                def _patched(model_path, sess_options=None, providers=None, **kw):
                    providers = [("DmlExecutionProvider", {"device_id": 0})] + (providers or [])
                    return _orig(model_path, sess_options=sess_options,
                                 providers=providers, **kw)
                _patched._sky_dml_patched = True
                _u.InferenceSession = _patched
            dml_on = True

        # 2) 修正 rec_keys_path -> keys_path（RapidOCR 只对 rec_model_path 去前缀）
        _orig_ur = _u.UpdateParameters.update_rec_params
        if not getattr(_orig_ur, "_sky_keys_patched", False):
            def _ur_patched(self, config, rec_dict):
                config = _orig_ur(self, config, rec_dict)
                if "rec_keys_path" in config:
                    config["keys_path"] = config.pop("rec_keys_path")
                return config
            _ur_patched._sky_keys_patched = True
            _u.UpdateParameters.update_rec_params = _ur_patched
    except Exception as e:
        # 补丁失败原本是静默的：程序照跑、但会悄悄退回 CPU，日志里查不出原因
        print("  [PD] DirectML/字典补丁失败，OCR 将走 CPU 或退回 v3: %s" % e)
    return dml_on


def _tame_process(max_cores=None):
    """Windows: 降低本进程优先级（让游戏优先），并把进程钉在若干核上。
    限核数默认按 CPU 总数自适应给一半（至少2、最多 n-1）；可用环境变量
    SKY_LIMIT_CORES 覆盖（=0 表示不限核、只降优先级）。
    背景：早期写死 2 核，在游戏满载时会把 OCR/看门线程饿到单次推理十几秒；
    经基准实测 onnxruntime 线程数与耗时无关（2/4/8 线程同为约 2s），故不改引擎线程。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        proc = k32.GetCurrentProcess()
        n = os.cpu_count() or 4
        if max_cores is None:
            env = os.environ.get("SKY_LIMIT_CORES", "").strip()
            if env:
                max_cores = int(env)
            else:
                max_cores = max(2, min(n - 1, n // 2))
        k32.SetPriorityClass(proc, 0x00004000)  # BELOW_NORMAL：游戏优先抢 CPU
        if max_cores and 0 < max_cores < n:
            cores = max_cores
            mask = 0
            for i in range(n - cores, n):  # 用最后几个核，避开系统偏爱的 0 号核
                mask |= 1 << i
            k32.SetProcessAffinityMask(proc, mask)
            print(f"  [PD] 进程已限核 {cores}/{n}，优先级低于游戏（SKY_LIMIT_CORES 可调）")
        else:
            print(f"  [PD] 不限核（{n} 核全可用），优先级低于游戏")
    except Exception as e:
        print(f"  [PD] 进程限核失败（继续运行）: {e}")


_tame_process()


def _log_ocr_engine(eng):
    """打印 OCR 引擎【实际生效】的模型与推理设备。

    用模型指纹判断而不是回显请求值——请求 v5 却静默退回 v3、请求 GPU 却走了 CPU，
    这两件事以前都看不出来（程序照样跑，只是变慢变差）。启动时 3 个调用点各打一行，
    真机核对"三个 OCR 引擎是否都上了 GPU/是否真用了 v5"就看这三行。
    """
    try:
        if eng is None:
            print("  [PD] OCR 引擎: 不可用（RapidOCR 未安装）")
            return
        classes = eng.text_recognizer.session.session.get_outputs()[0].shape[-1]
        model = {18385: "PP-OCRv5", 6625: "PP-OCRv3"}.get(classes, "未知(%d 类)" % classes)
        prov = eng.text_detector.infer.session.get_providers()[0]
        extra = ""
        if prov == "CPUExecutionProvider" and OCR_DEVICE in ("auto", "dml"):
            try:
                import onnxruntime as _ort
                if "DmlExecutionProvider" not in _ort.get_available_providers():
                    extra = "（未检测到 DirectML，GPU 加速不可用：pip install onnxruntime-directml）"
            except Exception:
                pass
        print("  [PD] OCR 引擎: %s + %s%s" % (model, prov, extra))
    except Exception as e:
        print("  [PD] OCR 引擎: 已创建（状态读取失败: %s）" % e)


def make_ocr_engine():
    """创建 RapidOCR。默认优先 DirectML(GPU)，失败回退 CPU；
    SKY_OCR_MODEL=v5 且 models/ocr 文件齐全时用 PP-OCRv5，否则回退自带 v3。
    建完会打一行"实际生效"日志（见 _log_ocr_engine）。"""
    if RapidOCR is None:
        return None
    _patch_rapidocr_dml()

    use_v5 = OCR_MODEL == "v5" and all(os.path.exists(p) for p in _OCR_V5.values())
    if OCR_MODEL == "v5" and not use_v5:
        print("  [PD] 未找到 models/ocr 下的 v5 模型，回退自带 v3")

    eng = None
    # v5 路线（含逐级回退）
    if use_v5:
        try:
            eng = RapidOCR(det_model_path=_OCR_V5["det"],
                           rec_model_path=_OCR_V5["rec"],
                           rec_keys_path=_OCR_V5["keys"])
        except Exception as e:
            print("  [PD] v5 引擎创建失败，回退 v3:", e)

    # v3 路线（GPU 优先，失败回退 CPU，CPU 限线程）
    if eng is None and OCR_DEVICE in ("auto", "dml"):
        try:
            import onnxruntime as _ort
            if "DmlExecutionProvider" in _ort.get_available_providers():
                eng = RapidOCR()
        except Exception:
            pass
    if eng is None:
        try:
            eng = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        except Exception:
            eng = RapidOCR()
    _log_ocr_engine(eng)
    return eng


# ===================== 配置 =====================

@dataclass
class DetectorConfig:
    # ROI 均为相对坐标 (x0, y0, x1, y1)
    roi_chat: tuple = (0.01, 0.30, 0.40, 0.90)      # 完整聊天面板（消息区，左边界0.015→0.01防止第一个字被吞，0.005太靠左会不识别）
    roi_chat_input: tuple = (0.030, 0.80, 0.320, 0.96)  # 输入框搜索区（在此纵向找最黑横条，免疫窗口偏移）
    # chat 模板（输入框左角+铅笔图标）专属搜索区：x 必须从 0 起——图标从
    # x≈0.006 就开始，用 roi_chat/roi_chat_input 搜会削掉半个图标（07-03 教训）
    roi_chat_box: tuple = (0.0, 0.76, 0.34, 0.99)
    roi_hint_bar: tuple = (0.0, 0.950, 0.32, 0.999)  # 底部按键提示行(ESC退后/ENTER聊天/T语音输入)，续28主锚点
    chat_input_height: float = 0.030  # 输入框高度约占帧高 3%
    roi_dialog: tuple = (0.28, 0.28, 0.72, 0.75)    # 居中弹窗
    roi_f_prompt: tuple = (0.12, 0.15, 0.88, 0.85)  # 角色附近的 F 提示（扩大范围，覆盖玩家可能出现的位置）
                                                    # （y0 0.42→0.28：牵手图标偏高，07-03 实拍 y≈0.34）
    # 好友树判据＝右上角标题栏"人形+齿轮"标识：真机1080p两帧模板分0.998、中心(0.832,0.032)、
    # 1:1命中；聊天/普通帧最高仅0.66且误报点在顶部正中(0.50)被本ROI切掉。ROI只包右上顶部条。
    roi_friend_tree: tuple = (0.74, 0.00, 0.93, 0.10)

    # 聊天面板视觉阈值（2026-07-02 夜景+亮景实拍标定）。
    # 判据 = 满宽暗行：输入框横贯搜索带（行暗占比 0.88~1.0），
    # 消息气泡最宽只到 0.80 —— 借此区分输入框和关面板后渐隐的残留气泡。
    # edge 特征已废弃：星空场景边缘密度反而高于面板文字。
    chat_row_full: float = 0.85       # 单行暗占比超过此值 = "满宽暗行"
    chat_band_on: float = 0.75        # 满宽行窗口密度 → 开（降低阈值减少假阴性，宁可误判开着）
    chat_band_off: float = 0.30       # 低于此 → 关
    chat_wod_support: float = 0.002   # 消息区"暗底白字"佐证，防纯黑场景误判
    chat_band_bright: float = 0.001   # 条带内亮点（铅笔图标/占位文字）佐证（降低阈值更敏感）
    # 输入框肚子基本是空的（夜 0.003 / 亮主题 0.020），消息气泡塞满白字（0.029-0.073）。
    # 面板关着时别人的悬浮消息气泡会伪装成输入框——靠这条区分（2026-07-02 实测）
    # 注意：亮箱 0.020 与气泡 0.029 之间余量不大，待实拍悬浮气泡样本后复核
    chat_band_text: float = 0.025     # 条带内亮点超过此值 = 气泡/打字中，不表态
    chat_glow_veto: float = 0.35      # 判关时条带白光占比超过此值 = 疑似火光晃瞎
                                      # （实测：正常关 0.000，举火 0.709~1.000）
    chat_glow_local: float = 40.0     # 火光必须是局部辉光：条带均值比全帧均值高出
                                      # 此值才否决（白天全局亮 diff≈4，不触发）
    # 亮景浅色主题（2026-07-03 实拍标定）：场景够亮时游戏把面板换成浅色主题，
    # 输入框变为 ~90 的中灰条（绝对阈值 <80 漏网），但仍是"最暗的满宽横条"。
    # 改用相对阈值：暗 = 灰度 < 全帧均值 - delta。实测 开:帧169/框88-94，
    # 关:帧186/草地181+ → delta=55 两边余量都 >25。
    chat_light_frame_min: float = 140.0  # 全帧均值超过此值才启用浅色主题通道
                                         # （昨日亮景 113 仍是深色主题，夜景 53-94）
    chat_light_dark_delta: float = 55.0  # 相对暗阈值：frame_mean - delta
    loading_dark: float = 0.92        # 全帧暗占比
    loading_edge: float = 0.012       # 全帧边缘密度上限
    # 白屏过场（回家/传送有时是白色淡入淡出）。2026-07-03 全样本量化：
    # 正常场景纯白占比 ≤0.047 / 边缘 ≥0.016，双条件余量悬崖级
    loading_white: float = 0.85       # 全帧纯白(>235)占比
    loading_white_edge: float = 0.010 # 白屏时的边缘密度上限
    dialog_suspect_edge: float = 0.020  # 中央 ROI 有结构 → 触发弹窗 OCR

    # 模板匹配
    template_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    template_ref_height: int = 1080
    template_threshold: float = 0.82
    # chat 铅笔图标模板专用阈值：07-03 全样本回归 命中带 0.83~0.92 /
    # 误报带 ≤0.48，0.75 两头都有余量。主治浅色主题+局部暗景的"开着判关"
    chat_template_threshold: float = 0.75
    # f_prompt（牵手/点火F提示）专用阈值：牵手图标在不同背景下匹配分约0.60~0.70，
    # 全局0.82太高导致漏检。0.58 能命中且误报风险低（F提示图标形状独特）
    f_prompt_threshold: float = 0.70
    template_scales: tuple = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
    template_every_n: int = 3         # 每N帧才匹配一次（matchTemplate较贵，与游戏同机要省CPU）

    # 防抖 / TTL（秒）
    debounce_on: int = 2              # 连续 N 帧为真才置真
    debounce_off: int = 3
    loading_off_frames: int = 12      # 传送动画黑屏/亮屏交替，退出 LOADING 要更迟钝
    chat_off_frames: int = 8          # 点火/烟花等亮光特效会照亮输入框零点几秒，判关要扛住闪光
    ttl_confirm: float = 2.5          # OCR 证据有效期，过期自动回落 False
    ttl_friend_tree: float = 4.0
    ttl_f_prompt: float = 1.2

    # OCR 调度
    ocr_full_interval: float = 2.5    # 全帧 OCR 周期
    ocr_dialog_cooldown: float = 0.8  # 弹窗疑似时的加急 OCR 冷却
    ocr_min_confidence: float = 0.35

    # 自采集
    sense_interval: float = 0.10
    window_titles: tuple = ("光·遇", "Sky: Children of the Light", "光遇", "Sky")
    window_refresh: float = 2.0       # 跟随窗口模式下重新定位的周期

    debug: bool = False
    # debug 时 chat 每次翻转自动存整帧（悬浮消息位置不固定，只有全帧才留得住证据）
    flip_snapshot_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flip_debug")  # 绝对路径：从任意工作目录启动都落到本项目根
    flip_snapshot_keep: int = 80  # 多攒开/关翻转帧用于离线标定聊天面板锚点


# OCR 关键词表
CONFIRM_KWS = ("确认", "是否", "接受", "取消", "前往", "传送",
               "加入", "同意", "拒绝", "邀请", "好友请求", "献礼", "收下")
# 传送/加载动画里的过场文字含"加入"等词，会冒充弹窗（2026-07-02 实测）
CONFIRM_BLACKLIST = ("正在加入", "加入中", "正在前往", "正在加载", "加载中")
HOME_KWS = ("遇境",)
FRIEND_TREE_KWS = ("牵手", "拥抱", "击掌", "送礼", "解锁", "加深关系",
                   "亲密", "蜡烛", "爱心")
CHAT_UI_KWS = ("语音输入", "发送")   # "聊天"太泛，须出现在输入框位置才算（见 ingest_ocr）


# ===================== 场景枚举 =====================

class Screen(Enum):
    UNKNOWN = auto()
    LOADING = auto()
    HOME = auto()       # 遇境
    IN_WORLD = auto()   # 普通游戏场景


# ===================== 布尔通道（防抖 + TTL） =====================

class BoolChannel:
    """一个可防抖、可 TTL 衰减的布尔状态通道。

    feed():     每帧的弱证据（视觉/模板），tri-state，走防抖计数。
    evidence(): 强证据（OCR 命中），立即置真并续期；TTL 到期自动回落。
    """

    def __init__(self, name: str, on_frames: int = 2, off_frames: int = 3,
                 ttl: float | None = None):
        self.name = name
        self.on_frames = on_frames
        self.off_frames = off_frames
        self.ttl = ttl
        self.value = False
        self.confidence = 0.0
        self.source = ""
        self.since = 0.0        # 当前值维持的起点
        self.updated = 0.0      # 最近一次收到证据的时间
        self._on = 0
        self._off = 0
        self._expire = 0.0

    def feed(self, raw: bool | None, conf: float, source: str, now: float) -> bool:
        """弱证据。raw=None 表示本帧不表态。返回值是否翻转。"""
        changed = False
        if raw is True:
            self._on += 1
            self._off = 0
            if not self.value and self._on >= self.on_frames:
                self.value = True
                self.since = now
                changed = True
            if self.value:
                self.confidence = conf
                self.source = source
                self.updated = now
                # 持续为真的弱证据也续期/清除到期时间
                self._expire = now + self.ttl if self.ttl else 0.0
        elif raw is False:
            self._off += 1
            self._on = 0
            # TTL 通道由强证据维持，弱证据 False 不立即打断，交给到期回落
            if self.value and not self.ttl and self._off >= self.off_frames:
                self.value = False
                self.since = now
                self.confidence = conf
                self.source = source
                changed = True
        # raw None: 不动计数
        changed |= self._tick(now)
        return changed

    def evidence(self, conf: float, source: str, now: float,
                 ttl: float | None = None) -> bool:
        """强证据：立即置真。"""
        changed = not self.value
        self.value = True
        if changed:
            self.since = now
        self.confidence = max(self.confidence, conf) if not changed else conf
        self.source = source
        self.updated = now
        self._on = 0
        self._off = 0
        t = ttl if ttl is not None else self.ttl
        if t:
            self._expire = now + t
        return changed

    def _tick(self, now: float) -> bool:
        # 注意看 _expire 而非 self.ttl：evidence(ttl=...) 可以给无 ttl 通道挂临时到期
        if self.value and self._expire and now > self._expire:
            self.value = False
            self.since = now
            self.source = "ttl_expired"
            self.confidence = 0.0
            return True
        return False

    def tick(self, now: float) -> bool:
        return self._tick(now)


# ===================== WorldState =====================

@dataclass(frozen=True)
class ChannelSnap:
    value: bool
    confidence: float
    source: str
    since: float
    updated: float


@dataclass(frozen=True)
class WorldSnapshot:
    ts: float
    frame_seq: int
    screen: Screen
    screen_since: float
    chat_open: ChannelSnap
    friend_tree: ChannelSnap
    confirm_dialog: ChannelSnap
    confirm_text: str            # 最近一次弹窗 OCR 原文（判断按空格还是按 enter 用）
    confirm_keywords: tuple
    f_prompt: ChannelSnap
    f_prompt_name: str           # 命中的 F 图标模板名（f_icon=点火 / f_hand=牵手）
    metrics: dict                # 本帧视觉指标，调试用

    def describe(self) -> str:
        parts = [f"screen={self.screen.name}"]
        # chat 永远明示开/关，只在为真时才显示的写法会让"关"看起来像没在工作
        c = self.chat_open
        parts.append(f"chat={'开' if c.value else '关'}"
                     + (f"({c.source} {c.confidence:.2f})" if c.value else ""))
        for name, ch in (("tree", self.friend_tree),
                         ("confirm", self.confirm_dialog),
                         ("f", self.f_prompt)):
            if ch.value:
                parts.append(f"{name}√({ch.source} {ch.confidence:.2f})")
        if self.confirm_dialog.value and self.confirm_keywords:
            parts.append("kw=" + "/".join(self.confirm_keywords))
        return " ".join(parts)


class WorldState:
    """线程安全的世界状态。决策层只调 snapshot()，不接触截图。"""

    def __init__(self, cfg: DetectorConfig):
        self._lock = threading.Lock()
        self._cfg = cfg
        d_on, d_off = cfg.debounce_on, cfg.debounce_off
        self.chat = BoolChannel("chat", d_on, cfg.chat_off_frames)
        self.friend_tree = BoolChannel("friend_tree", d_on, d_off,
                                       ttl=cfg.ttl_friend_tree)
        self.confirm = BoolChannel("confirm", d_on, d_off, ttl=cfg.ttl_confirm)
        self.f_prompt = BoolChannel("f_prompt", d_on, d_off,
                                    ttl=cfg.ttl_f_prompt)
        self._loading = BoolChannel("loading", d_on, cfg.loading_off_frames)
        self.screen = Screen.UNKNOWN
        self.screen_since = time.time()
        self.confirm_text = ""
        self.confirm_keywords: tuple = ()
        # 弹窗视觉疑似状态（由 process_frame 每帧更新）：OCR 关键词必须配合视觉疑似，
        # 否则聊天内容里的"接受/加入/邀请"等词会误判成弹窗、自动按空格
        self.dialog_visual_score = 0.0   # 最近 confirm 模板匹配分
        self.dialog_edge_suspect = False # 弹窗区域边缘疑似
        self.f_prompt_name = ""
        self.frame_seq = 0
        self.ts = 0.0
        self.metrics: dict = {}
        self.on_change: Optional[Callable[[str, Any, Any, "WorldState"], None]] = None

    # --- 写入（仅 PanelDetector 调用） ---

    def _emit(self, name: str, old, new):
        cb = self.on_change
        if cb and old != new:
            try:
                cb(name, old, new, self)
            except Exception:
                pass

    def set_screen(self, screen: Screen, now: float):
        with self._lock:
            old = self.screen
            if screen != old:
                self.screen = screen
                self.screen_since = now
        self._emit("screen", old, screen)

    # --- 读取 ---

    def snapshot(self) -> WorldSnapshot:
        def snap(ch: BoolChannel) -> ChannelSnap:
            return ChannelSnap(ch.value, ch.confidence, ch.source,
                               ch.since, ch.updated)
        with self._lock:
            return WorldSnapshot(
                ts=self.ts,
                frame_seq=self.frame_seq,
                screen=self.screen,
                screen_since=self.screen_since,
                chat_open=snap(self.chat),
                friend_tree=snap(self.friend_tree),
                confirm_dialog=snap(self.confirm),
                confirm_text=self.confirm_text,
                confirm_keywords=self.confirm_keywords,
                f_prompt=snap(self.f_prompt),
                f_prompt_name=self.f_prompt_name,
                metrics=dict(self.metrics),
            )


# ===================== 模板库 =====================

class TemplateBank:
    """templates/<channel>/*.png，灰度多尺度匹配。目录不存在则静默禁用。"""

    CHANNELS = ("chat", "friend_tree", "confirm", "f_prompt", "home", "hint_bar")

    def __init__(self, cfg: DetectorConfig):
        self.cfg = cfg
        # (模板名, 灰度图)：名字用于区分同通道内不同图标（点火 vs 牵手）
        self.bank: dict[str, list[tuple[str, np.ndarray]]] = {}
        self._scaled_cache: dict[tuple, list[tuple[str, np.ndarray]]] = {}
        for ch in self.CHANNELS:
            paths = sorted(glob.glob(os.path.join(cfg.template_dir, ch, "*.png")))
            imgs = []
            for p in paths:
                img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
                if img is not None and img.size > 0:
                    name = os.path.splitext(os.path.basename(p))[0]
                    imgs.append((name, img))
            if imgs:
                self.bank[ch] = imgs
        if self.bank and cfg.debug:
            print(f"  [Tmpl] 已加载: "
                  + ", ".join(f"{k}x{len(v)}" for k, v in self.bank.items()))

    def has(self, channel: str) -> bool:
        return channel in self.bank

    def _scaled(self, channel: str, frame_h: int) -> list[np.ndarray]:
        key = (channel, frame_h)
        cached = self._scaled_cache.get(key)
        if cached is not None:
            return cached
        base = frame_h / self.cfg.template_ref_height
        out = []
        for name, tmpl in self.bank.get(channel, []):
            for s in self.cfg.template_scales:
                f = base * s
                w = max(8, int(tmpl.shape[1] * f))
                h = max(8, int(tmpl.shape[0] * f))
                out.append((name, cv2.resize(tmpl, (w, h), interpolation=cv2.INTER_AREA)))
        self._scaled_cache[key] = out
        return out

    def match_named(self, gray_roi: np.ndarray, channel: str,
                    frame_h: int) -> tuple[float, str]:
        """返回该通道在 ROI 内的 (最高匹配分, 命中模板名)，无模板返回 (-1, '')。"""
        if channel not in self.bank:
            return -1.0, ""
        best, best_name = 0.0, ""
        for name, tmpl in self._scaled(channel, frame_h):
            th, tw = tmpl.shape[:2]
            if th >= gray_roi.shape[0] or tw >= gray_roi.shape[1]:
                continue
            res = cv2.matchTemplate(gray_roi, tmpl, cv2.TM_CCOEFF_NORMED)
            score = float(res.max())
            if score > best:
                best, best_name = score, name
        return best, best_name

    def match(self, gray_roi: np.ndarray, channel: str, frame_h: int) -> float:
        return self.match_named(gray_roi, channel, frame_h)[0]


# ===================== 窗口定位（仅 Windows） =====================

def find_game_client_region(titles: tuple) -> dict | None:
    """找光遇窗口的客户区（不含标题栏/边框），返回屏幕物理像素坐标。

    比 pygetwindow 的整窗矩形准：标题栏高度不会混进 ROI，
    这是"截图吞边角"的根源。找不到窗口返回 None。
    """
    import sys as _sys
    if not _sys.platform.startswith("win"):
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()   # 拿物理像素，和 mss 对齐
    except Exception:
        pass

    # 排除控制台窗口：cmd 标题里往往带着脚本路径（可能含"光遇"），会自匹配
    console_classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
    candidates: list[tuple[int, int, str]] = []   # (优先级, hwnd, title)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if not title:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in console_classes:
            return True
        for pri, t in enumerate(titles):
            if title == t:
                candidates.append((pri, hwnd, title))
                break
            # 子串匹配只允许长标题："Sky"会误中 Skype、"光遇"会误中
            # 浏览器攻略标签（07-03 实锤：检测器跟着 M365 广告窗跑了）
            if len(t) >= 8 and t in title:
                candidates.append((pri + 100, hwnd, title))
                break
        return True

    user32.EnumWindows(enum_cb, 0)
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    hwnd = candidates[0][1]

    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 200 or h < 200:      # 最小化/异常
        return None
    return {"left": pt.x, "top": pt.y, "width": w, "height": h}


# ===================== 检测器 =====================

def _crop(frame: np.ndarray, roi: tuple) -> np.ndarray:
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = roi
    return frame[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]


def judge_friend_tree_menu(texts, fh=1080, fw=1920, y_min=0.90, x_min=0.55):
    """好友树/右侧网格交互菜单"已打开"判据（纯函数，可单测）。

    真机特征：菜单打开时右下角是固定提示行"信息 Q / 选择 SPACE / 退后 ESC"；
    聊天面板底部是"ESC退后 / ENTER聊天 / T语音输入"，没有"选择"，借此区分。
    要求"选择"与("退后"或"SPACE")同时落在右下底部区域，避免游戏世界文字误命中。
    texts: [{"text","x","y",...}]，x/y 为 OCR 框左上角像素坐标。"""
    bottom = []
    for it in (texts or []):
        try:
            x = float(it.get("x", -1)); y = float(it.get("y", -1))
        except (TypeError, ValueError):
            continue
        if y >= fh * y_min and x >= fw * x_min:
            bottom.append(str(it.get("text", "")))
    joined = "".join(bottom).upper().replace(" ", "")
    if "选择" not in joined:
        return False
    return ("退后" in joined) or ("SPACE" in joined)


class PanelDetector:
    def __init__(self, region: dict | None = None,
                 cfg: DetectorConfig | None = None,
                 follow_window: bool = False):
        self.cfg = cfg or DetectorConfig()
        self.world = WorldState(self.cfg)
        self.templates = TemplateBank(self.cfg)
        self._region = region
        self._follow_window = follow_window   # 运行中周期性重新定位游戏窗口
        self._last_window_check = 0.0
        self._window_missing = False          # 游戏窗口不在（最小化等）→ 暂停感知
        self._ocr_q: queue.Queue = queue.Queue(maxsize=2)
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._ocr_engine = None
        self._last_full_ocr = 0.0
        self._last_dialog_ocr = 0.0
        self._last_fmiss_log = 0.0
        self._latest_frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._pending_emits: list[tuple] = []

    # ---------- 生命周期 ----------

    def start(self):
        """自采集模式：截屏线程 + OCR 线程。"""
        if self._region is None and self._follow_window:
            self._region = find_game_client_region(self.cfg.window_titles)
        if self._region is None:
            raise RuntimeError("没找到光遇窗口；请先开游戏，"
                               "或手动传 region={'left','top','width','height'}")
        self._stop.clear()
        t = threading.Thread(target=self._sense_loop, name="PD-Sense", daemon=True)
        t.start()
        self._threads.append(t)
        self.start_ocr()

    def start_ocr(self):
        """外部喂帧模式：只起 OCR 线程。"""
        if any(t.name == "PD-OCR" and t.is_alive() for t in self._threads):
            return
        t = threading.Thread(target=self._ocr_loop, name="PD-OCR", daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3.0)
        self._threads.clear()

    def latest_frame(self) -> np.ndarray | None:
        """最近一帧的拷贝，供外部（如聊天 OCR）使用。"""
        with self._frame_lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def friend_tree_score_now(self, frame: np.ndarray | None = None):
        """对指定帧（默认最新截屏）同步跑一次好友树右上角标识模板，返回 (score, name)。

        好友树动作在关键几秒内要确定结果，而 process_frame 的 run_tmpl 每 N 帧才匹配
        一次、与游戏同机时还会被慢 OCR 挤占 CPU——真机出现过"好友树已打开、后台
        tmpl_tree 却一直 None、4 秒超时反被 ESC 关掉"（2026-09-09）。动作线程用本方法
        主动识别，不依赖后台调度节奏；离线真机好友树帧 0.998、非好友树帧 0.19。"""
        if not self.templates.has("friend_tree"):
            return -1.0, ""
        if frame is None:
            frame = self.latest_frame()
        if frame is None:
            return -1.0, ""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self.templates.match_named(
            _crop(gray, self.cfg.roi_friend_tree), "friend_tree", frame.shape[0])

    # ---------- 每帧主流程 ----------

    def process_frame(self, frame: np.ndarray, now: float | None = None):
        """喂入一帧 BGR。视觉 + 模板同步跑完，OCR 按需异步排队。"""
        now = now or time.time()
        with self._frame_lock:
            self._latest_frame = frame
        w = self.world
        with w._lock:
            w.frame_seq += 1
            w.ts = now
            seq = w.frame_seq
        run_tmpl = (seq % self.cfg.template_every_n == 0)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h = frame.shape[0]
        m: dict[str, float] = {}

        # ---- 1. Loading（全帧） ----
        dark_all = float(np.mean(gray < 40))
        edges_all = cv2.Canny(cv2.resize(gray, None, fx=0.35, fy=0.35), 50, 120)
        edge_all = float(np.mean(edges_all > 0))
        m["dark_all"] = dark_all
        m["edge_all"] = edge_all
        bright_all = float(np.mean(gray > 235))
        m["bright_all"] = bright_all
        loading_raw = ((dark_all > self.cfg.loading_dark
                        and edge_all < self.cfg.loading_edge)
                       or (bright_all > self.cfg.loading_white
                           and edge_all < self.cfg.loading_white_edge))
        with w._lock:
            loading_changed = w._loading.feed(loading_raw, dark_all, "visual", now)
            loading_now = w._loading.value
        if loading_changed:
            if loading_now:
                w.set_screen(Screen.LOADING, now)
            else:
                # 出加载 → 场景待定，立刻全帧 OCR 重新定位
                w.set_screen(Screen.UNKNOWN, now)
                self._enqueue_ocr("full", now, force=True)

        if loading_now:
            # 加载中其余检测无意义，且黑屏会把各面板误判成关
            with w._lock:
                w.metrics = m
            return

        # ---- 2. 聊天面板（视觉 + 模板） ----
        chat_raw, chat_conf, chat_m = self._detect_chat(frame, gray)
        m.update(chat_m)
        if run_tmpl and self.templates.has("chat"):
            score = self.templates.match(
                _crop(gray, self.cfg.roi_chat_box), "chat", h)
            m["tmpl_chat"] = score
            if score >= self.cfg.chat_template_threshold:
                # 铅笔图标在位=面板铁定开着，主题/亮度无关。
                # 浅色主题在帧均<140 时视觉通道会漏（07-03 雨林/云雾实证），全靠它
                chat_raw, chat_conf = True, score
        # hint_bar 底部提示行主裁分（续28/续42/续43 接入）：面板开合由 sky-loop 的
        # panel_open 裁决，这里只算分。ROI 只有 614x52、模板尺度有限，每帧都算
        # （实测 <几ms），不占 run_tmpl 降频名额。无模板时 metrics 里没有该键。
        if self.templates.has("hint_bar"):
            m["hint_bar"] = self.hint_bar_score(gray)
        chat_flipped = False
        chat_new = None
        with w._lock:
            if w.chat.feed(chat_raw, chat_conf, "visual", now):
                self._pending_emits.append(("chat", not w.chat.value, w.chat.value))
                chat_flipped = True
                chat_new = w.chat.value
        # 注意别拿新值当"是否翻转"的哨兵：翻到 None(看不清) 会被漏存，
        # 而雨林暗景最可能产生的恰是这种翻转
        if chat_flipped and self.cfg.debug:
            self._save_flip_snapshot(frame, chat_new, now, chat_m)

        # ---- 3. 确认弹窗（视觉疑似 → 加急 OCR；模板可直判） ----
        dlg_gray = _crop(gray, self.cfg.roi_dialog)
        dlg_edges = cv2.Canny(dlg_gray, 50, 120)
        dlg_edge = float(np.mean(dlg_edges > 0))
        m["dlg_edge"] = dlg_edge
        dialog_suspect = dlg_edge > self.cfg.dialog_suspect_edge
        if run_tmpl and self.templates.has("confirm"):
            score = self.templates.match(dlg_gray, "confirm", h)
            m["tmpl_confirm"] = score
            if score >= self.cfg.template_threshold:
                with w._lock:
                    changed = w.confirm.evidence(score, "template", now)
                if changed:
                    w._emit("confirm", False, True)
                dialog_suspect = True
        with w._lock:
            w.dialog_visual_score = float(m.get("tmpl_confirm", 0.0))
            w.dialog_edge_suspect = bool(dialog_suspect)
            active_confirm = w.confirm.value
        if dialog_suspect or active_confirm:
            if now - self._last_dialog_ocr > self.cfg.ocr_dialog_cooldown:
                self._last_dialog_ocr = now
                self._enqueue_ocr("dialog", now)

        # ---- 4. F 交互提示（模板为主；降频匹配所以用即时证据+TTL，而非逐帧防抖） ----
        if run_tmpl and self.templates.has("f_prompt"):
            score, tname = self.templates.match_named(
                _crop(gray, self.cfg.roi_f_prompt), "f_prompt", h)
            m["tmpl_f"] = score
            if score >= self.cfg.f_prompt_threshold:
                with w._lock:
                    changed = w.f_prompt.evidence(score, "template", now)
                    w.f_prompt_name = tname
                if changed:
                    w._emit("f_prompt", False, True)
            elif self.cfg.debug and score >= 0.60 \
                    and now - self._last_fmiss_log > 2.0:
                # 排查"牵手图标为什么没触发"：实机分数低于阈值时留痕
                self._last_fmiss_log = now
                print(f"[PD] f_prompt 差点命中: {tname} {score:.2f} "
                      f"(阈值 {self.cfg.f_prompt_threshold})")

        # ---- 5. 好友树（模板辅助；主证据来自 OCR） ----
        if run_tmpl and self.templates.has("friend_tree"):
            score = self.templates.match(
                _crop(gray, self.cfg.roi_friend_tree), "friend_tree", h)
            m["tmpl_tree"] = score
            if score >= self.cfg.template_threshold:
                with w._lock:
                    changed = w.friend_tree.evidence(score, "template", now)
                if changed:
                    w._emit("friend_tree", False, True)

        # ---- 6. TTL 到期回落 + 周期全帧 OCR ----
        with w._lock:
            for ch, name in ((w.confirm, "confirm"),
                             (w.friend_tree, "friend_tree"),
                             (w.f_prompt, "f_prompt")):
                if ch.tick(now):
                    self._pending_emits.append((name, True, False))
            w.metrics = m
        self._flush_emits(w)

        if now - self._last_full_ocr > self.cfg.ocr_full_interval:
            self._enqueue_ocr("full", now)

        # 场景兜底：出加载后一段时间没有任何 HOME 证据 → 视为 IN_WORLD
        with w._lock:
            scr, since = w.screen, w.screen_since
        if scr == Screen.UNKNOWN and now - since > 5.0:
            w.set_screen(Screen.IN_WORLD, now)

    def dump_evidence(self, tag: str = "probe"):
        """外部主动取证：最新帧+当前 chat 指标存进 flip_debug。
        给调度器守门按 C 前调用——"状态冻结型"误判（雨林暗景：面板开着
        却一直判关）不产生翻转，光靠翻转存证一张都逮不到（07-03 Aki 实测
        跑一轮雨林零存证）。文件名 {tag}_时分秒_b暗带_t亮点.jpg"""
        if not self.cfg.debug:
            return
        frame = self.latest_frame()
        if frame is None:
            return
        with self.world._lock:
            m = dict(self.world.metrics)
        d = self.cfg.flip_snapshot_dir
        try:
            os.makedirs(d, exist_ok=True)
            ts = time.strftime("%H%M%S")
            b = int(m.get("chat_band", 0) * 100)
            t = int(m.get("chat_bright", 0) * 1000)
            path = os.path.join(d, f"{tag}_{ts}_b{b}_t{t}.jpg")
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            old = sorted(glob.glob(os.path.join(d, f"{tag}_*.jpg")))
            for p in old[:-self.cfg.flip_snapshot_keep]:
                os.unlink(p)
        except Exception as e:
            print(f"  [PD] 取证失败: {e}")

    def _save_flip_snapshot(self, frame, value, now, metrics):
        """chat 翻转瞬间存整帧。文件名只用 ASCII：Windows 下 cv2 写中文路径会静默失败。"""
        d = self.cfg.flip_snapshot_dir
        try:
            os.makedirs(d, exist_ok=True)
            ts = time.strftime("%H%M%S", time.localtime(now))
            tag = {True: "on", False: "off"}.get(value, "unk")
            b = int(metrics.get("chat_band", 0) * 100)
            t = int(metrics.get("chat_bright", 0) * 1000)
            path = os.path.join(d, f"chat_{ts}_{tag}_b{b}_t{t}.jpg")
            okw = cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not okw:
                print(f"  [PD] 翻转存证写入失败(imwrite返回False): {path}")
            else:
                print(f"  [PD] chat 翻转存证: {path}")
            old = sorted(glob.glob(os.path.join(d, "chat_*.jpg")))
            for p in old[:-self.cfg.flip_snapshot_keep]:
                os.unlink(p)
        except Exception as e:
            print(f"  [PD] 存证失败: {e}")

    def _flush_emits(self, w: WorldState):
        pending, self._pending_emits = self._pending_emits, []
        for pe in pending:
            w._emit(*pe)

    def hint_bar_score(self, gray_full):
        """续28/续43主锚点：底部按键提示行模板相关分（多尺度取最高）。
        未放置 templates/hint_bar/*.png 时返回 None。
        续43 起 process_frame 每帧调用、写 metrics["hint_bar"]，由 sky-loop.panel_open
        做三态主裁（开>=0.50 / 关<=0.20 / 中间带回退旧融合）。"""
        if gray_full is None or not self.templates.has("hint_bar"):
            return None
        h = gray_full.shape[0]
        return self.templates.match(_crop(gray_full, self.cfg.roi_hint_bar), "hint_bar", h)

    # ---------- 视觉：聊天面板（沿用 sky-loop 经验阈值） ----------

    def _detect_chat(self, frame, gray_full):
        """主特征 = 输入框条带：面板开时"聊天……"输入框是一整条近黑横条。
        佐证 = 消息区暗底白字（泡泡里的字）或条带内亮点（铅笔图标）。"""
        cfg = self.cfg
        strip = _crop(gray_full, cfg.roi_chat_input)
        # 在搜索区内纵向滑窗，找"满宽暗行"最密集的一条横带 = 输入框实际位置。
        # 满宽判据排除残留气泡；不写死纵坐标，窗口化/标题栏/分辨率变化都不影响。
        rows_dark = np.mean(strip < 80, axis=1)
        qualified = (rows_dark > cfg.chat_row_full).astype(np.float32)
        k = max(3, int(gray_full.shape[0] * cfg.chat_input_height))
        if len(qualified) > k:
            smooth = np.convolve(qualified, np.ones(k) / k, mode="valid")
            mx = float(smooth.max())
            # 并列极大时取最靠下的窗口：输入框永远是面板最底部的满宽元素，
            # 悬浮消息气泡都在它上面
            best = int(np.where(smooth >= mx - 0.02)[0][-1])
            band_dark = mx
            win = strip[best:best + k]
            qmask = qualified[best:best + k] > 0
            # 亮点只统计满宽行本身，窗口里混进的窄气泡/背景行不算
            band = win[qmask] if qmask.any() else win
        else:
            band = strip
            band_dark = float(qualified.mean())
        band_bright = float(np.mean(band > 140))
        msg = _crop(gray_full, cfg.roi_chat)
        bright_m = (msg > 190).astype(np.uint8)
        dark_m = cv2.dilate((msg < 80).astype(np.uint8), np.ones((9, 9), np.uint8))
        wod = float(np.mean((bright_m & dark_m) > 0))
        m = {"chat_band": band_dark, "chat_wod": wod, "chat_bright": band_bright}
        if band_dark > cfg.chat_band_on and len(qualified) > k:
            # 主窗口（最靠下的并列极大窗）按原判据表态：
            if band_bright > cfg.chat_band_text:
                # 黑带里全是白字：气泡（面板可能关着）或打字中（面板开着）
                return None, 0.5, m
            if band_bright > cfg.chat_band_bright:
                # 铅笔图标/占位文字的亮点是输入框的身份证（实测 开=0.016/0.003）
                # 但必须同时有消息区暗底白字佐证，防止暗场景地面/UI误判
                if wod > cfg.chat_wod_support:
                    return True, band_dark, m
                else:
                    # 没有消息区佐证，可能是暗场景误判，不表态
                    return None, 0.5, m
            # 主窗无斑点 → 两种可能：①暗场景地面/水面（07-03 遇境黑夜误判开的
            # 根源，夜景星光只污染消息区 wod、污染不了条带）；②输入框在上方，
            # 被下方并列的暗地面抢走了主窗。自下而上扫其余满宽暗窗找输入框：
            idxs = np.where(smooth >= cfg.chat_band_on)[0]
            brs = []
            for i in idxs:
                wwin = strip[i:i + k]
                qm = qualified[i:i + k] > 0
                bw = wwin[qm] if qm.any() else wwin
                brs.append(float(np.mean(bw > 140)))
            # 白字窗（打字中/气泡）的位置：紧挨着它的"干净小窗"只是打字框的
            # 边沿缝隙，不能当输入框（否则打字否决被钻空子）
            text_pos = [i for i, br in zip(idxs, brs) if br > cfg.chat_band_text]
            for i, br in sorted(zip(idxs, brs), reverse=True):   # 自下而上
                if (cfg.chat_band_bright < br <= cfg.chat_band_text
                        and all(abs(int(i) - int(j)) >= k for j in text_pos)):
                    m["chat_bright"] = br
                    # 同样需要消息区暗底白字佐证
                    if wod > cfg.chat_wod_support:
                        return True, band_dark, m
                    else:
                        return None, 0.5, m
            if text_pos:
                return None, 0.5, m      # 只有打字/气泡窗 → 不表态
            m["chat_bright"] = 0.0
            # 满宽暗带遍布却无一有图标亮点 → 暗场景地面，面板是关的
            # （不回 None：否则暗图里状态永远冻结，守门永远不开面板）
            return False, 0.9, m
        if band_dark < cfg.chat_band_off:
            frame_mean = float(gray_full[::4, ::4].mean())
            if band_bright > cfg.chat_glow_veto:
                # 条带被大片白光淹没（举火/烟花）→ 只是被晃瞎，不表态。
                # 但必须是"局部辉光"（条带亮、全帧仍暗）才算火光；
                # 白天全屏都亮时不能否决，否则亮景判关被永久卡死
                # （2026-07-03 亮景实战教训：只靠 band_bright 一个条件会误伤白天）
                if float(band.mean()) > frame_mean + cfg.chat_glow_local:
                    return None, 0.5, m
            # 浅色主题通道：亮景下面板换浅色，输入框是"相对暗"的中灰满宽条
            if frame_mean > cfg.chat_light_frame_min:
                thr = frame_mean - cfg.chat_light_dark_delta
                rows_rel = np.mean(strip < thr, axis=1)
                qual_r = (rows_rel > cfg.chat_row_full).astype(np.float32)
                if len(qual_r) > k:
                    smooth_r = np.convolve(qual_r, np.ones(k) / k, mode="valid")
                    mx_r = float(smooth_r.max())
                    m["chat_rel"] = mx_r
                    if mx_r > cfg.chat_band_on:
                        best_r = int(np.where(smooth_r >= mx_r - 0.02)[0][-1])
                        win_r = strip[best_r:best_r + k]
                        qm_r = qual_r[best_r:best_r + k] > 0
                        band_r = win_r[qm_r] if qm_r.any() else win_r
                        # 佐证：框内铅笔图标/占位文字的亮点（实测 231 的白色像素）
                        if float(np.mean(band_r > 140)) > cfg.chat_band_bright:
                            return True, mx_r, m
                        return None, 0.5, m   # 有满宽灰条但无图标亮点 → 存疑
            return False, 1.0 - band_dark, m
        return None, 0.0, m

    # ---------- OCR ----------

    def _enqueue_ocr(self, kind: str, now: float, force: bool = False):
        try:
            self._ocr_q.put_nowait(kind)
        except queue.Full:
            if force:
                try:
                    self._ocr_q.get_nowait()
                    self._ocr_q.put_nowait(kind)
                except queue.Empty:
                    pass

    def _ensure_ocr_engine(self):
        if self._ocr_engine is None:
            if RapidOCR is None:
                raise RuntimeError("rapidocr_onnxruntime 未安装，OCR 层不可用")
            self._ocr_engine = make_ocr_engine()
        return self._ocr_engine

    def _ocr_loop(self):
        try:
            self._ensure_ocr_engine()
        except RuntimeError as e:
            print(f"  [PD-OCR] {e}（仅视觉+模板运行）")
            return
        while not self._stop.is_set():
            try:
                kind = self._ocr_q.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._frame_lock:
                frame = self._latest_frame
            if frame is None:
                continue
            try:
                if kind == "dialog":
                    items = self._run_ocr(_crop(frame, self.cfg.roi_dialog))
                else:
                    self._last_full_ocr = time.time()
                    items = self._run_ocr(frame)
                self.ingest_ocr(items, scope=kind)
            except Exception as e:
                print(f"  [PD-OCR] 异常: {e}")

    def _run_ocr(self, img: np.ndarray) -> list[dict]:
        h = img.shape[0]
        if h < 500:  # 小图放大，识别率更好
            img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            scale = 2
        else:
            scale = 1
        result, _ = self._ensure_ocr_engine()(img)
        items = []
        for r in result or []:
            box, text, conf = r[0], str(r[1]), float(r[2])
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            items.append({"text": text.strip(), "confidence": conf,
                          "x": int(min(xs) / scale), "y": int(min(ys) / scale)})
        return items

    def ingest_ocr(self, items: list[dict], scope: str = "full"):
        """消化一批 OCR 结果（内部 OCR 线程和外部 read_screen 都走这里）。"""
        now = time.time()
        cfg = self.cfg
        texts = [it for it in items
                 if it.get("text") and float(it.get("confidence", 0)) >= cfg.ocr_min_confidence]
        joined = "\n".join(it["text"] for it in texts)
        w = self.world

        # 确认弹窗：命中动作关键词 + 排除纯聊天面板文本/过场动画文字/加载画面
        hit_confirm = tuple(k for k in CONFIRM_KWS if k in joined)
        is_chat_ui_only = ("发送" in joined and not hit_confirm)
        is_transition = any(b in joined for b in CONFIRM_BLACKLIST)
        with w._lock:
            in_loading = (w.screen == Screen.LOADING)
            at_home = (w.screen == Screen.HOME)
        if at_home:
            # 遇境星盘上常驻"传送"二字，会冒充弹窗（2026-07-03 实测：
            # 回家后 kw=传送 反复触发接弹窗，space+f 全按在星盘上）。
            # 在家时"传送"不算证据；真弹窗还有模板匹配和其他关键词兜底。
            hit_confirm = tuple(k for k in hit_confirm if k != "传送")
        # 全屏 OCR 必须配合视觉疑似（聊天内容里的"接受/加入/邀请"等不算弹窗）；
        # dialog 加急 OCR 本就是视觉疑似后才跑，直接确认
        if scope == "full":
            visual_suspect = (
                w.dialog_visual_score >= self.cfg.template_threshold * 0.7
                or w.dialog_edge_suspect)
        else:
            visual_suspect = True
        if hit_confirm and not is_chat_ui_only and not is_transition \
                and not in_loading and visual_suspect:
            with w._lock:
                changed = w.confirm.evidence(0.9, f"ocr:{scope}", now)
                w.confirm_text = joined[:300]
                w.confirm_keywords = hit_confirm
            if changed:
                w._emit("confirm", False, True)
        elif hit_confirm and not visual_suspect:
            print(f"  [PD] 命中确认词『{'/'.join(hit_confirm)}』但无弹窗视觉特征，"
                  f"判为聊天内容、不按空格")

        if scope != "full":
            return

        # 场景：遇境。"遇境"必须是独立短标签才算数——回家确认弹窗写着
        # "要回到遇境吗"，句子里的"遇境"不是到家证据（07-03 Aki 实测：
        # 弹窗一出 screen 就被误判成 HOME，人还站在雨林）
        home_hit = any(
            k in t and len(t) <= 4 and not any(c in t for c in "要回到吗?？")
            for t in (it["text"].strip() for it in texts)
            for k in HOME_KWS)
        if home_hit:
            with w._lock:
                cur, since = w.screen, w.screen_since
            # 从世界直接变"到家"必须先过 LOADING——雨林走不回遇境，聊天文本里
            # 的"遇境"二字不能把人瞬移回家（07-03 假 HOME 让回家被跳过）。
            # IN_WORLD 刚成立 15s 内例外：脚本在遇境启动时 UNKNOWN 5s 就被
            # 兜底成 IN_WORLD，第一轮全帧 OCR 要能把它纠正回 HOME
            if cur in (Screen.UNKNOWN, Screen.HOME) \
                    or (cur == Screen.IN_WORLD and now - since < 15.0):
                w.set_screen(Screen.HOME, now)
        elif w.screen == Screen.UNKNOWN and any(k in joined for k in CHAT_UI_KWS):
            # 有游戏内 UI 文本但没有遇境 → 普通场景
            w.set_screen(Screen.IN_WORLD, now)

        # 好友树已打开：主判据是右上角"人形+齿轮"逐帧模板（templates/friend_tree，离线
        # 0.998、非树帧<0.61，见续35/续37）。这里只保留极难误判的">=2 专属关键词"作弱兜底。
        # 续33 的底部提示行 OCR 判据 judge_friend_tree_menu 已停用：真机 2026-09-09 在根本
        # 没开好友树（聊天面板开着、没按F）的切换帧被 OCR 瞬时误识成 tree√(ocr:menu)，会诱发
        # watch 误按 ESC（甚至弹设置）；静态聊天帧虽不复现，但 OCR 异步+切换帧不可控，而模板
        # 在同一误判帧正确地没有触发——故好友树只信模板。纯函数 judge_* 与单测保留备用。
        tree_hits = tuple(k for k in FRIEND_TREE_KWS if k in joined)
        if len(tree_hits) >= 2:
            with w._lock:
                changed = w.friend_tree.evidence(0.9, "ocr", now)
            if changed:
                w._emit("friend_tree", False, True)

        fh, fw = (self._latest_frame.shape[:2]
                  if self._latest_frame is not None else (1080, 1920))

        # 聊天面板 UI 词兜底；"聊天"是占位文字，必须落在输入框位置才算数
        chat_hit = any(k in joined for k in CHAT_UI_KWS)
        if not chat_hit:
            bx0, by0, bx1, by1 = cfg.roi_chat_input
            for it in texts:
                if it["text"].startswith("聊天") \
                        and fw * bx0 <= it["x"] <= fw * bx1 \
                        and fh * (by0 - 0.02) <= it["y"] <= fh * (by1 + 0.02):
                    chat_hit = True
                    break
        if chat_hit:
            # 视觉果断说"关"（输入框位置空空如也）时否决 OCR 兜底：
            # OCR 有秒级延迟，关面板瞬间容易拿到渐隐中的旧画面文字
            with w._lock:
                band_now = w.metrics.get("chat_band")
                vetoed = band_now is not None and band_now < cfg.chat_band_off
                if not vetoed:
                    changed = w.chat.evidence(0.8, "ocr", now, ttl=5.0)
            if not vetoed and changed:
                w._emit("chat", False, True)

        # F 提示兜底：中央区域一个孤立的 "F"
        x0, y0, x1, y1 = cfg.roi_f_prompt
        for it in texts:
            if it["text"].upper() == "F" \
                    and fw * x0 <= it["x"] <= fw * x1 \
                    and fh * y0 <= it["y"] <= fh * y1:
                with w._lock:
                    changed = w.f_prompt.evidence(it["confidence"], "ocr", now,
                                                  ttl=cfg.ttl_f_prompt)
                if changed:
                    w._emit("f_prompt", False, True)
                break

    # ---------- 自采集线程 ----------

    def _make_mss_grabber(self):
        """GDI 后备截屏。每次 BitBlt 都会让显卡停顿一下，游戏会偶尔掉帧。"""
        import mss as mss_lib
        sct = mss_lib.mss()

        def grab(region):
            shot = sct.grab(region)
            img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(
                shot.height, shot.width, 4)
            return np.ascontiguousarray(img[:, :, :3])

        print("  [PD-Sense] 截屏后端: mss/GDI（游戏可能偶尔掉帧，装 dxcam 可消除: pip install dxcam）")
        return grab

    def _make_grabber(self):
        """优先 DXGI 桌面复制（OBS 游戏捕获同原理，对游戏零干扰），
        bettercam 是 dxcam 的维护版分支、接口相同，谁装了用谁。
        都不可用则回退 mss/GDI。返回 grab(region)->BGR ndarray | None(画面无变化)。"""
        for mod_name in ("bettercam", "dxcam"):
            try:
                mod = __import__(mod_name)
                cam = mod.create(output_color="BGR")
                if cam is None:
                    raise RuntimeError(f"{mod_name}.create 返回 None")

                def grab(region):
                    l, t = region["left"], region["top"]
                    f = cam.grab(region=(l, t, l + region["width"], t + region["height"]))
                    return f  # None = 这一瞬画面没变化，沿用上一帧即可

                print(f"  [PD-Sense] 截屏后端: {mod_name}/DXGI（对游戏零干扰）")
                return grab
            except Exception as e:
                print(f"  [PD-Sense] {mod_name} 不可用: {e}")
        return self._make_mss_grabber()

    def _sense_loop(self):
        grab = self._make_grabber()
        grab_fails = 0
        print("  [PD-Sense] 截屏线程启动")
        while not self._stop.is_set():
            t0 = time.time()
            # 跟随窗口：周期性重新定位，拖动/缩放/最大化都能跟上
            if self._follow_window and t0 - self._last_window_check > self.cfg.window_refresh:
                self._last_window_check = t0
                r = find_game_client_region(self.cfg.window_titles)
                if r:
                    if self._window_missing:
                        print("  [PD-Sense] 窗口回来了，恢复感知")
                        self._window_missing = False
                    if r != self._region:
                        print(f"  [PD-Sense] 窗口变化: {self._region} -> {r}")
                        self._region = r
                elif not self._window_missing:
                    # 旧区域现在显示的是别的窗口，继续看=对着广告做判断
                    # （07-03 实锤：读出"M365 高效工作"）。宁可闭眼不可幻视。
                    print("  [PD-Sense] 游戏窗口不见了（最小化？），暂停感知等它回来")
                    self._window_missing = True
            if self._window_missing:
                time.sleep(0.3)
                continue
            try:
                frame = grab(self._region)
                if frame is not None:
                    self.process_frame(np.ascontiguousarray(frame), t0)
                grab_fails = 0
            except Exception as e:
                grab_fails += 1
                print(f"  [PD-Sense] 截屏异常: {e}")
                if grab_fails >= 3:  # dxcam 连续翻车（多屏/越界等）→ 永久切回 GDI
                    grab = self._make_mss_grabber()
                    grab_fails = 0
                time.sleep(0.5)
            elapsed = time.time() - t0
            time.sleep(max(0.0, self.cfg.sense_interval - elapsed))
        print("  [PD-Sense] 截屏线程退出")


# ===================== CLI（标定 / 冒烟测试） =====================

def _parse_region(s: str) -> dict:
    l, t, w, h = (int(x) for x in s.split(","))
    return {"left": l, "top": t, "width": w, "height": h}


def _region_from_mcp(url: str, token: str) -> dict:
    import requests
    r = requests.post(url, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "status", "arguments": {}},
    }, headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=10)
    import json as _json
    text = "\n".join(i["text"] for i in r.json()["result"]["content"]
                     if i.get("type") == "text")
    win = _json.loads(text).get("window")
    if not win:
        raise RuntimeError("MCP status 里没有窗口信息")
    return {"left": win["left"], "top": win["top"],
            "width": win["width"], "height": win["height"]}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="光遇 PanelDetector 冒烟测试")
    ap.add_argument("--region", help="L,T,W,H 手动指定截屏区域（默认自动找游戏窗口）")
    ap.add_argument("--mcp-url", default="http://127.0.0.1:9900",
                    help="兜底：从 MCP status 拿窗口区域")
    ap.add_argument("--token", default="1234")
    ap.add_argument("--dump", action="store_true",
                    help="保存整帧和各 ROI 截图后退出（标定用）")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    cfg = DetectorConfig(debug=args.debug)
    follow = False
    if args.region:
        region = _parse_region(args.region)
        print(f"截屏区域(手动): {region}")
    else:
        region = find_game_client_region(cfg.window_titles)
        if region:
            follow = True
            print(f"截屏区域(自动跟随游戏窗口客户区): {region}")
        else:
            print("没找到光遇窗口，尝试 MCP...")
            region = _region_from_mcp(args.mcp_url, args.token)
            print(f"截屏区域(MCP，含标题栏可能有偏差): {region}")

    det = PanelDetector(region=region, cfg=cfg, follow_window=follow)

    if args.dump:
        import mss as mss_lib
        shot = mss_lib.mss().grab(region)
        frame = np.ascontiguousarray(np.frombuffer(
            shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3])
        cv2.imwrite("pd_frame.png", frame)
        for name, roi in (("chat", cfg.roi_chat), ("dialog", cfg.roi_dialog),
                          ("f_prompt", cfg.roi_f_prompt),
                          ("friend_tree", cfg.roi_friend_tree)):
            cv2.imwrite(f"pd_roi_{name}.png", _crop(frame, roi))
        print("已保存 pd_frame.png / pd_roi_*.png")
        return

    def on_change(name, old, new, world):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {name}: {old} -> {new}")

    det.world.on_change = on_change
    det.start()
    print("运行中，每 2 秒打印一次 WorldState，Ctrl+C 退出")
    try:
        while True:
            time.sleep(2.0)
            print("  " + det.world.snapshot().describe())
    except KeyboardInterrupt:
        det.stop()
        print("已退出")


if __name__ == "__main__":
    main()
