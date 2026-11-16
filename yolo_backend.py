# -*- coding: utf-8 -*-
"""
yolo_backend.py — sky-with-you YOLO 推理后端装配

提供"自动检测显卡 → 能用 GPU 就走 DirectML、否则 CPU"的加载逻辑，思路与 OCR 的
SKY_OCR_DEVICE 一致，但比 OCR 多一步物理显卡枚举（detect_gpu）。

设备开关 SKY_YOLO_DEVICE：
  - auto（默认）：检测到可用 DirectML 且存在同名 .onnx → GPU，否则回退 PyTorch CPU(.pt)
  - dml：强制 GPU（不可用则告警并回退 CPU）
  - cpu：强制 PyTorch CPU

要点：
  - DirectML 会话显式构造，并用 get_providers() 确认确实含 DmlExecutionProvider，
    防止 ultralytics 直接加载 onnx 静默走 CPU（实测那样反而更慢）。
  - DML 推理复用 panel_detector 的进程级串行锁，让 YOLO 与 OCR 的 DirectML 会话不并发，
    规避多会话并发崩 amdxc64.dll 的历史问题。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# 28 类权威类名（0-21 锁定，22-27 追加），与 data.yaml / openvino metadata.yaml 一致。
# 作为 metadata.yaml 缺失时的兜底，避免仅为拿类名而加载 torch。
_FALLBACK_NAMES = [
    "chat_input", "hand_icon", "open_tree_icon", "accept_hug", "accept_carry",
    "accept_high_five", "accept_fist_bump", "follow", "wing_light", "light",
    "focus", "seat", "bear_hug", "curtain_call", "fancy_high_five", "handshake",
    "head_pat", "mutual_light", "play_fight", "shoulder", "spin_dance", "whisper",
    "instrument", "settings_menu", "butterfly_lure", "action_cancel",
    "princess_hug", "duo_dance",
]

YOLO_DEVICE = os.environ.get("SKY_YOLO_DEVICE", "auto").lower().strip()


# ===================== 物理显卡检测（比 OCR 多的步骤）=====================
def detect_gpu():
    """枚举真实物理显卡，并判断 onnxruntime DirectML 是否可用。

    返回 (gpu_names, dml_available)：
      - gpu_names：去重后的真实显卡名称（已过滤 Idd/虚拟显示器、远程显示、软件渲染器）；
      - dml_available：onnxruntime 是否提供 DmlExecutionProvider（GPU 推理的权威信号）。
    任何探测失败都安全降级（返回空列表/False），不抛异常。
    """
    gpu_names = []
    if os.name == "nt":
        try:
            import subprocess
            ps = "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=15,
            )
            bad = ("idd", "oray", "remote", "virtual", "basic render",
                   "microsoft basic", "wan")
            for line in out.stdout.splitlines():
                nm = line.strip()
                if not nm:
                    continue
                low = nm.lower()
                if any(b in low for b in bad):
                    continue
                if nm not in gpu_names:
                    gpu_names.append(nm)
        except Exception as e:
            print("  [YOLO] 物理显卡枚举失败（继续按 DirectML 能力判断）: %s" % e)

    dml_available = False
    try:
        import onnxruntime as ort
        dml_available = "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        dml_available = False
    return gpu_names, dml_available


# ===================== 类名加载 =====================
def _load_class_names(onnx_path):
    """优先读同目录 openvino metadata.yaml 的 names，缺失/失败则用内置 28 类。"""
    names = None
    try:
        import yaml
        base = os.path.basename(onnx_path)
        if base.lower().endswith(".onnx"):
            base = base[:-5]
        meta = os.path.join(os.path.dirname(onnx_path),
                            base + "_openvino_model", "metadata.yaml")
        if os.path.exists(meta):
            with open(meta, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw = data.get("names") or {}
            if isinstance(raw, dict) and raw:
                names = {int(k): str(v) for k, v in raw.items()}
    except Exception:
        names = None
    if not names:
        names = {i: n for i, n in enumerate(_FALLBACK_NAMES)}
    return names


# ===================== 结果对象（模拟 ultralytics 接口）=====================
class _SkyBox:
    """单个检测框。

    为与下游索引方式一致，用 torch tensor：
      - xyxy shape (1,4)，支持 box.xyxy[0] 解包出 4 个坐标；
      - conf / cls shape (1,)，支持 box.conf[0] / box.cls[0]。
    """
    def __init__(self, xyxy, conf, cls):
        self.xyxy = xyxy
        self.conf = conf
        self.cls = cls


class _SkyBoxes:
    def __init__(self, boxes):
        self._boxes = boxes

    def __iter__(self):
        return iter(self._boxes)

    def __len__(self):
        return len(self._boxes)


class _SkyResult:
    def __init__(self, boxes, names):
        self.boxes = _SkyBoxes(boxes)
        self.names = names


# ===================== 共享串行锁 =====================
def _shared_dml_lock():
    """复用 panel_detector 的进程级串行锁，让 YOLO/OCR 的 DirectML 不并发。"""
    try:
        import panel_detector as _pd
        return getattr(_pd, "_OCR_INFER_LOCK", None)
    except Exception:
        return None


# ===================== ONNX DirectML / CPU 后端 =====================
class SkyYoloOnnx:
    """与 ultralytics YOLO 同形的可调用推理后端（单会话、串行）。"""

    def __init__(self, onnx_path, names, lock, force_dml=True):
        import onnxruntime as ort
        self.names = names
        self.overrides = {}
        self._lock = lock
        self._size_warned = False
        if force_dml:
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        self._sess = ort.InferenceSession(onnx_path, providers=providers)
        self._iname = self._sess.get_inputs()[0].name
        # 固定输入尺寸（onnx 以 dynamic=False 导出，只接受该尺寸）
        _shape = self._sess.get_inputs()[0].shape
        self._fixed_size = None
        try:
            _h, _w = int(_shape[2]), int(_shape[3])
            if _h > 0 and _w > 0:
                self._fixed_size = (_h, _w)
        except Exception:
            self._fixed_size = None
        actual = self._sess.get_providers()
        self.backend = "dml" if (force_dml and "DmlExecutionProvider" in actual) else "cpu"

    def __call__(self, frame, conf=0.5, imgsz=None, iou=0.45,
                 verbose=False, **kwargs):
        import numpy as np
        import torch
        from ultralytics.data.augment import LetterBox
        from ultralytics.utils.nms import non_max_suppression
        from ultralytics.utils.ops import scale_boxes

        if imgsz is None:
            imgsz = 960
        # 固定尺寸模型：忽略不匹配的 imgsz（如独立检查传 1280），统一按模型尺寸，避免报错
        if self._fixed_size is not None:
            if not self._size_warned and imgsz != self._fixed_size[0]:
                print("  [YOLO] ONNX 固定输入 %d，请求 imgsz=%s 已按模型尺寸处理"
                      % (self._fixed_size[0], imgsz))
                self._size_warned = True
            target = self._fixed_size
        else:
            target = (imgsz, imgsz) if isinstance(imgsz, int) else tuple(imgsz)

        # 预处理：LetterBox（与 ultralytics 同口径），BGR HWC -> RGB CHW
        lb = LetterBox(target, auto=False, stride=32)(image=frame)
        h1, w1 = lb.shape[:2]
        chw = lb.transpose(2, 0, 1)[::-1]
        x = np.ascontiguousarray(chw, dtype=np.float32)[None] / 255.0

        # 推理（DML 串行；无锁时直接跑）
        if self._lock is not None:
            with self._lock:
                raw = self._sess.run(None, {self._iname: x})[0]
        else:
            raw = self._sess.run(None, {self._iname: x})[0]

        # 后处理：NMS，再把坐标从 letterbox 尺寸缩回原图
        pred = non_max_suppression(
            torch.from_numpy(raw), conf_thres=conf, iou_thres=iou,
            nc=len(self.names),
        )[0]
        boxes = []
        if pred is not None and len(pred):
            pred[:, :4] = scale_boxes((h1, w1), pred[:, :4], frame.shape[:2])
            for b in pred:
                boxes.append(_SkyBox(
                    b[:4].unsqueeze(0).clone(),  # xyxy (1,4)
                    b[4:5].clone(),              # conf (1,)
                    b[5:6].clone(),              # cls  (1,)
                ))
        return [_SkyResult(boxes, self.names)]


# ===================== 路径推断 =====================
def _onnx_for_model(model_path):
    """根据 .pt 路径推断同名 .onnx；入参已是 onnx 或文件不存在则相应处理。"""
    if model_path.lower().endswith(".onnx"):
        return model_path if os.path.exists(model_path) else None
    cand = os.path.splitext(model_path)[0] + ".onnx"
    return cand if os.path.exists(cand) else None


# ===================== 装配入口 =====================
def load_yolo_model(model_path, device=None):
    """装配 YOLO 推理后端。

    返回 model 对象：
      - GPU 可用：SkyYoloOnnx（DirectML），其 .names/.overrides 与 ultralytics 同形；
      - 否则或任何失败：ultralytics YOLO(.pt)（PyTorch CPU）。
    """
    device = (device or YOLO_DEVICE).lower().strip()
    gpu_names, dml_available = detect_gpu()
    if gpu_names:
        print("  [YOLO] 检测到显卡: %s" % " / ".join(gpu_names))
    else:
        print("  [YOLO] 未枚举到独立物理显卡")
    print("  [YOLO] DirectML 可用: %s" % ("是" if dml_available else "否"))

    if device == "dml" and not dml_available:
        print("  [YOLO] 强制 GPU 但 DirectML 不可用，回退 CPU")
    if device == "cpu":
        dml_available = False

    want_gpu = device in ("auto", "dml") and dml_available
    onnx_path = _onnx_for_model(model_path) if want_gpu else None
    if want_gpu and not onnx_path:
        print("  [YOLO] 未找到对应 .onnx，回退 CPU(.pt)")

    if want_gpu and onnx_path:
        try:
            names = _load_class_names(onnx_path)
            m = SkyYoloOnnx(onnx_path, names, _shared_dml_lock(), force_dml=True)
            if m.backend == "dml":
                print("  [YOLO] 推理后端: DirectML(GPU) + ONNX")
                return m
            print("  [YOLO] DML 会话未真正生效，回退 CPU")
        except Exception as e:
            print("  [YOLO] DirectML 后端初始化失败，回退 CPU: %s" % e)

    from ultralytics import YOLO
    print("  [YOLO] 推理后端: PyTorch CPU + .pt")
    return YOLO(model_path)
