# -*- coding: utf-8 -*-
"""
doctor.py — 光遇 AI 伙伴 环境自检（只读，不改任何东西）

在学校/家里换电脑、或者跑不起来时，先执行：  py doctor.py

八项检查（互相独立，一项崩了不影响其它项）：
  1. Python 版本 / 位数
  2. 依赖包（必需 + 可选降级项 + 截屏后端）
  3. 关键文件（key.txt / persona.txt / YOLO 模型 / 模板 / 长期记忆）
  4. Arduino 硬件串口（含 SKY_SERIAL_PORT 核对）
  5. 游戏窗口客户区          <- 新增
  6. MCP 服务 127.0.0.1:9900（连通 + 身份 + token + 按键后端）<- 加强
  7. 当前配置回显（含编码/风险提示）
  8. 核心源码语法

本文件的四条硬约定（改它之前先读）：
  * 只用标准库 —— 依赖没装好时也必须能跑出结果，绝不能 import numpy/cv2/panel_detector
    （panel_detector 在 import 期就会限核/降优先级，而且缺 cv2 直接抛）。
  * 只读 —— 不写文件、不建目录、不改环境变量；语法检查走内存 compile()，不落 __pycache__。
  * 单项独立 —— 每项检查都包在 _guard 里，抛异常只记一条错误，其余检查照跑完（见 _guard）。
  * 所有输出都走 Report.out，检查只依赖注入的 Probes —— 这样单测能把真实环境整个换掉。

退出码：
  0 = 没有阻断性错误（可能有 WARN）
  1 = 有 ERROR（加了 --strict 时 WARN 也算）
  2 = 自检自身异常（结果不完整，要修 doctor.py）

用法：
  py doctor.py
  py doctor.py --strict
  py doctor.py --root D:\\path\\to\\another\\copy
"""
import argparse
import importlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))

MCP_HOST = "127.0.0.1"
MCP_PORT = 9900
# sky-loop-v7.py 里 MCP_TOKEN 写死 1234，start_mcp.bat 也用 --token 1234
MCP_TOKEN_DEFAULT = "1234"

# 与 panel_detector.DetectorConfig.window_titles 保持一致
WINDOW_TITLES = ("光·遇", "Sky: Children of the Light", "光遇", "Sky")
# 排除控制台窗口：cmd 标题里往往带着脚本路径（可能含"光遇"），会自匹配
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}

ARDUINO_VIDS = {0x2341, 0x1B4F, 0x239A}
CH340_VID = 0x1A86

MODEL_REL = os.path.join("runs", "detect", "runs", "detect", "train", "weights", "best.pt")

SOURCE_FILES = ("sky-loop-v7.py", "sky-mcp-server.py", "panel_detector.py", "mem_reader.py")
CORE_FILES = ("llm_client.py", "memory.py", "reply_engine.py", "style_learner.py",
              "vision_client.py", "web_search.py")

# 模板目录 -> 缺了会怎样（每一句都对应真实降级，别写成空话）
TEMPLATE_DIRS = {
    "hint_bar": "面板开合的三态主裁失效，退回旧 YOLO+像素融合（历史上反复踩的坑）",
    "f_prompt": "F 提示（牵手/点火图标）识别不到，牵手只能靠 YOLO",
    "chat": "聊天面板铅笔图标佐证没了，浅色主题下容易判关",
    "confirm": "确认弹窗少了模板佐证，只剩边缘疑似 + OCR 关键词",
    "friend_tree": "好友树右上角标识没了，抱抱动作会判定不出树有没有开",
}


class Report:
    """收集 OK/WARN/ERR 三种结果，并保持原有打印格式。

    所有输出都过 self.out，单测可以塞一个 list.append 把输出抓走。
    crashed 单独记：那是"自检自己出问题"，不是环境问题，退出码要区分开。
    """

    def __init__(self, out=None):
        self.out = out if out is not None else print
        self.oks = []
        self.warns = []
        self.errors = []
        self.crashed = []

    def ok(self, m):
        self.oks.append(m)
        self.out("  [ OK ] " + m)

    def warn(self, m):
        self.warns.append(m)
        self.out("  [WARN] " + m)

    def err(self, m):
        self.errors.append(m)
        self.out("  [ERR ] " + m)

    def crash(self, m):
        self.crashed.append(m)
        self.errors.append(m)
        self.out("  [ERR ] " + m)

    def section(self, t):
        self.out("\n" + "=" * 58 + "\n" + t + "\n" + "=" * 58)


# ===================== 探针（唯一碰真实环境的地方） =====================

class Probes:
    """所有会碰真实环境的动作都集中在这里，单测用 FakeProbes 整个替换。

    约定：单个探针允许抛异常（由 _guard 兜成一条错误），但"环境本来就可能没有"
    的情况（如 pyserial 没装、端口没开）应该在检查函数里自己 try 成 WARN。
    """

    def __init__(self, root=ROOT, env=None):
        self.root = root
        self.env = dict(os.environ) if env is None else dict(env)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    # --- 基础 ---
    def python_info(self):
        v = sys.version_info
        return {"major": v.major, "minor": v.minor, "micro": v.micro,
                "bits": 64 if sys.maxsize > 2 ** 32 else 32,
                "platform": sys.platform,
                "impl": sys.implementation.name,
                "exe": sys.executable}

    def import_module(self, name):
        """返回版本字符串；导入失败抛异常。"""
        mod = importlib.import_module(name)
        return str(getattr(mod, "__version__", "") or "")

    def stat(self, path):
        """返回 (是否存在, 字节数)；读不到一律当不存在。"""
        try:
            return True, int(os.stat(path).st_size)
        except Exception:
            return False, 0

    def read_bytes(self, path, limit=1 << 20):
        try:
            with open(path, "rb") as f:
                return f.read(limit)
        except Exception:
            return b""

    def read_text(self, path, limit=4096):
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                return f.read(limit)
        except Exception:
            return ""

    def list_dir(self, path):
        try:
            return sorted(os.listdir(path))
        except Exception:
            return []

    def compile_source(self, path):
        """语法检查（纯内存，不写 .pyc，保住'只读'承诺）。失败抛 SyntaxError/OSError。"""
        with open(path, "rb") as f:
            src = f.read()
        compile(src, path, "exec")   # bytes 入参，PEP 263 编码声明照样生效
        return len(src)

    # --- 串口 ---
    def list_ports(self):
        """返回 [{'device','vid','description'}]；pyserial 缺失时抛异常。"""
        from serial.tools import list_ports
        out = []
        for p in list_ports.comports():
            out.append({"device": p.device, "vid": p.vid,
                        "description": p.description or ""})
        return out

    # --- 窗口（Windows 专用；实现对齐 panel_detector.find_game_client_region） ---
    def find_game_window(self, titles=WINDOW_TITLES, console_classes=CONSOLE_CLASSES):
        """找光遇窗口客户区（不含标题栏），返回 dict 或 None（非 Windows 也返回 None）。"""
        if not sys.platform.startswith("win"):
            return None
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()   # 拿物理像素
        except Exception:
            pass

        candidates = []   # (优先级, hwnd, title)

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
                # 子串匹配只允许长标题："Sky"会误中 Skype（07-03 实锤）
                if len(t) >= 8 and t in title:
                    candidates.append((pri + 100, hwnd, title))
                    break
            return True

        user32.EnumWindows(enum_cb, 0)
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        _, hwnd, title = candidates[0]

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return {"title": title, "left": int(pt.x), "top": int(pt.y),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top)}

    # --- 网络 ---
    def tcp_open(self, host, port, timeout=0.6):
        """返回 (是否可连, 失败原因)。"""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True, ""
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e)
        finally:
            try:
                s.close()
            except Exception:
                pass

    def http_json(self, url, payload=None, token=None, timeout=3.0):
        """GET（无 payload）/ POST（有 payload）一个 JSON 接口。

        返回 (http状态码|None, 解析出的 dict|None, 错误说明)。任何异常都吞成说明，
        绝不往外抛 —— 检查函数只关心"通没通、是不是那个服务"。
        """
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            url, data=data, headers=headers,
            method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(1 << 20)
                try:
                    return int(r.status), json.loads(raw.decode("utf-8", "replace")), ""
                except Exception:
                    return int(r.status), None, "返回不是合法 JSON"
        except urllib.error.HTTPError as e:
            return int(e.code), None, "HTTP %s" % e.code
        except Exception as e:
            return None, None, "%s: %s" % (type(e).__name__, e)


# ===================== 纯函数小工具 =====================

def looks_like_mojibake(text):
    """检测"UTF-8 字节被按 ANSI/GBK 读"形成的乱码。

    真实坑：bat 文件编码不统一时（start_loop_mem.bat 是 UTF-8，其余是 GBK），
    cmd 按 ANSI 读会得到 '鐝傜弬' 这种串，env 里的白名单就再也匹配不上玩家名。
    判据：按 GBK 编回去能解成另一串合法中文 —— 只做提示，不做判定。
    """
    if not text or text.isascii():
        return False
    for enc in ("gbk", "cp936", "latin-1"):
        try:
            raw = text.encode(enc)
        except Exception:
            continue
        try:
            fixed = raw.decode("utf-8")
        except Exception:
            continue
        if fixed != text and any("\u4e00" <= c <= "\u9fff" for c in fixed):
            return True
    return False


def _try_import(probes, module):
    """返回 (是否可用, 版本或错误说明)。绝不抛异常。"""
    try:
        return True, probes.import_module(module)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ===================== 各项检查 =====================
# 约定：每个 check_xxx(report, probes) 只做一件事，内部自己 try 掉"环境本来就可能没有"
# 的情况；真正意外的异常留给 _guard 记成"自检自身异常"。

def check_python(report, probes):
    info = probes.python_info()
    print_major = "%d.%d.%d" % (info["major"], info["minor"], info["micro"])
    report.out("  当前 Python %s（%d 位, %s, %s）"
               % (print_major, info["bits"], info["platform"], info["impl"]))
    if (info["major"], info["minor"]) >= (3, 10):
        report.ok("版本 >= 3.10，语法兼容")
    else:
        report.err("Python 版本过低，需要 3.10 及以上（代码用了 X | None 类型语法）")
    if info["bits"] != 64:
        report.warn("不是 64 位 Python —— onnxruntime/opencv 的轮子基本都是 64 位，OCR 会装不上")
    if info["impl"] != "cpython":
        report.warn("不是 CPython（%s）—— 依赖轮子多为 CPython 编译，可能装不上" % info["impl"])


REQUIRED = {
    "numpy": "numpy", "cv2": "opencv-python", "mss": "mss",
    "rapidocr_onnxruntime": "rapidocr_onnxruntime", "openai": "openai",
    "requests": "requests", "serial": "pyserial", "pyperclip": "pyperclip",
    "pyautogui": "pyautogui", "pygetwindow": "PyGetWindow",
    "win32api": "pywin32", "pydirectinput": "pydirectinput",
}
OPTIONAL = {
    "ultralytics": "ultralytics（YOLO，缺了退回模板匹配）",
    "torch": "torch（ultralytics 的推理后端）",
    "PIL": "Pillow（MCP 的截图工具用，主循环不用）",
}


def check_deps(report, probes):
    for mod, pipname in REQUIRED.items():
        good, ver = _try_import(probes, mod)
        if good:
            report.ok("%-24s %s" % (pipname, ver))
        else:
            report.err("%-24s 未安装/导入失败：%s  -> py -m pip install %s"
                       % (pipname, ver, pipname))
    for mod, desc in OPTIONAL.items():
        good, ver = _try_import(probes, mod)
        if good:
            report.ok("%s %s" % (desc, ver))
        else:
            report.warn("%s —— 未安装；YOLO 会退回模板匹配（要 YOLO 就 py -m pip install ultralytics）"
                        % desc)

    # 截屏后端：两者是替代关系，至少有一个就够，不能逐个报缺失
    dxgi = [m for m in ("bettercam", "dxcam") if _try_import(probes, m)[0]]
    if dxgi:
        report.ok("截屏后端 %s（DXGI，对游戏零干扰）" % "/".join(dxgi))
    else:
        report.warn("bettercam/dxcam 都没装 —— 退回 mss/GDI 截屏，每次 BitBlt 会让游戏偶尔掉帧"
                    "（想更稳：py -m pip install bettercam）")


def check_llm_key(report, probes):
    env_key = (probes.env.get("OPENROUTER_API_KEY") or "").strip()
    if env_key:
        report.ok("LLM Key 来自环境变量 OPENROUTER_API_KEY（长度 %d，内容不打印）" % len(env_key))
        return
    p = probes.path("key.txt")
    exists, _size = probes.stat(p)
    if not exists:
        report.err("既没有 key.txt 也没有环境变量 OPENROUTER_API_KEY，AI 回复会失败"
                   "（在项目根目录建 key.txt，只写一行 API key）")
        return
    content = probes.read_text(p).strip()
    if not content:
        report.err("key.txt 是空文件（只有空白/换行）—— 把 API key 单独一行写进去，否则 LLM 无法鉴权")
        return
    low = content.lower()
    placeholders = ("你的key", "your", "xxx", "api_key", "apikey", "填入", "在这里")
    hit = [b for b in placeholders if b in low]
    if hit:
        report.err("key.txt 看起来还是占位符（命中 %s）—— 换成真实 key" % "/".join(hit))
        return
    lines = [ln for ln in content.splitlines() if ln.strip()]
    tips = []
    if len(content) < 20:
        tips.append("长度只有 %d，偏短" % len(content))
    if len(lines) != 1:
        tips.append("有 %d 行非空内容（程序把整个文件 strip 后当 key 用）" % len(lines))
    if " " in content.strip():
        tips.append("中间有空格")
    if tips:
        report.warn("key.txt 格式存疑：" + "、".join(tips))
    else:
        # 只回显前 3 位（"sk-" 这种前缀），其余隐去，避免自检输出泄漏密钥
        report.ok("key.txt 存在（长度 %d，前缀 %s***，其余隐去）" % (len(content), content[:3]))


def _check_model(report, probes):
    yolo_on = probes.env.get("SKY_YOLO_ENABLED", "1") != "0"
    rel = MODEL_REL
    exists, size = probes.stat(probes.path(rel))
    if not exists:
        if yolo_on:
            report.warn("缺少 %s —— YOLO 不可用，牵手/好友树入口退回模板匹配"
                        "（桌面备份 yolo_3classes_negatives_best.pt 拷回来即可）" % rel)
        else:
            report.ok("SKY_YOLO_ENABLED=0，未启用 YOLO（不需要模型文件）")
        return
    head = probes.read_bytes(probes.path(rel), 4)
    if size < 100 * 1024:
        report.err("%s 只有 %d 字节，疑似占位/截断的坏模型 -> 重新拷一份 best.pt" % (rel, size))
    elif head[:2] != b"PK":
        report.warn("%s 不是常见的 torch 存档（zip 头是 %r），可能损坏或非 torch.save 产物"
                    % (rel, head))
    else:
        report.ok("%s 存在（%d 字节，zip 头正常）" % (rel, size))
    if yolo_on and not _try_import(probes, "ultralytics")[0]:
        report.warn("有 YOLO 模型但没装 ultralytics —— 模型加载会被跳过，仍走模板匹配")


def _check_templates(report, probes):
    for name, why in TEMPLATE_DIRS.items():
        pics = [f for f in probes.list_dir(probes.path("templates", name))
                if f.lower().endswith(".png")]
        if pics:
            report.ok("templates/%s/  %d 张模板" % (name, len(pics)))
        else:
            report.warn("templates/%s/ 没有 png —— %s" % (name, why))


def _check_user_data(report, probes):
    rel = os.path.join("user_data", "memory.json")
    exists, size = probes.stat(probes.path(rel))
    if exists and size > 0:
        report.ok("%s 存在（%d 字节，AI 长期记忆；换电脑记得一起带走）" % (rel, size))
    else:
        report.warn("%s 不存在 —— 首次运行会自动新建（空白记忆）；"
                    "如果本该有，检查是不是换电脑没同步" % rel)


def _need(report, probes, rel, hint=""):
    exists, size = probes.stat(probes.path(rel))
    if exists and size > 0:
        report.ok("%s  存在（%d 字节）" % (rel, size))
    else:
        report.err("缺少 %s %s" % (rel, hint))


def check_files(report, probes):
    check_llm_key(report, probes)
    _need(report, probes, "persona.txt",
          hint="（把 persona.example.txt 复制成 persona.txt 并填写人设；缺了主程序直接退出）")
    _check_model(report, probes)
    _check_user_data(report, probes)
    _check_templates(report, probes)
    exists, size = probes.stat(probes.path("yolov8n.pt"))
    if exists and size > 0:
        report.ok("yolov8n.pt  存在（%d 字节，YOLO 训练基座，只在重训时需要）" % size)
    else:
        report.warn("缺少 yolov8n.pt —— 只在重新训练 YOLO 时才需要，日常挂机不影响")


def check_serial(report, probes):
    want = (probes.env.get("SKY_SERIAL_PORT") or "").strip()
    try:
        ports = probes.list_ports()
    except Exception as e:
        report.warn("串口枚举失败（pyserial 没装好？）：%s —— 没有硬件也能跑，"
                    "把 SKY_INPUT_BACKEND 换成 pydirectinput 即可" % e)
        return

    hit = None
    for p in ports:
        tag = []
        if p.get("vid") in ARDUINO_VIDS or "arduino" in (p.get("description") or "").lower():
            hit = p["device"]
            tag.append("<= 命中 Arduino")
        if p.get("vid") == CH340_VID:
            tag.append("CH340")
        report.out("  %-6s VID=%-8s %s %s" % (
            p.get("device", "?"),
            hex(p["vid"]) if p.get("vid") else "-",
            p.get("description", ""), " ".join(tag)))

    if not ports:
        report.out("  （当前没有任何串口设备）")

    if want:
        devices = {str(p.get("device", "")).upper() for p in ports}
        if want.upper() in devices:
            report.ok("SKY_SERIAL_PORT=%s 已插着" % want)
        else:
            report.warn("SKY_SERIAL_PORT=%s 不在当前串口列表里 —— 板子没插？还是 COM 号变了？"
                        "（留空会自动探测，建议清掉这个环境变量）" % want)

    if hit:
        report.ok("自动探测将选中 %s" % hit)
        return
    ch340 = [p for p in ports if p.get("vid") == CH340_VID]
    if len(ch340) == 1:
        report.ok("单个 CH340，将按山寨 Pro Micro 选中 %s" % ch340[0]["device"])
    else:
        backend = probes.env.get("SKY_INPUT_BACKEND", "arduino")
        extra = ""
        if backend == "arduino":
            extra = ("；当前 SKY_INPUT_BACKEND=arduino，按键时会直接报 'Arduino ... 探测不到'，"
                     "不会自动退回软件模拟 —— 要么插板子，要么改用 start_loop_no_hardware.bat")
        report.warn("没探测到 Arduino（没插不影响启动，但硬件按键不可用）" + extra)


def check_window(report, probes):
    info = probes.python_info()
    if not str(info.get("platform", "")).startswith("win"):
        report.warn("非 Windows 平台，跳过游戏窗口检查（本项目只支持光遇 PC 版）")
        return
    win = probes.find_game_window()
    if not win:
        report.warn("没找到光遇窗口 —— 自检可以先不开游戏；正式挂机前必须先把游戏开起来"
                    "（窗口标题要含 '光·遇'，且不能最小化）")
        return
    w, h = int(win.get("width", 0)), int(win.get("height", 0))
    report.ok("找到游戏窗口「%s」客户区 %dx%d @(%s,%s)"
              % (win.get("title", ""), w, h, win.get("left"), win.get("top")))
    if w < 200 or h < 200:
        report.err("游戏窗口客户区只有 %dx%d，疑似最小化/异常 —— panel_detector 会直接拒绝启动"
                   % (w, h))
    elif h < 720:
        report.warn("游戏窗口高度 %d 偏小 —— 模板按 1080p 标定，小窗口下 ROI/模板分可能不准" % h)
    elif (w, h) != (1920, 1080):
        report.warn("客户区 %dx%d 不是标定的 1920x1080 —— 模板会按帧高缩放到 %.3f 倍，"
                    "偏离越多插值误差越大；跑 `py window_fix.py --apply` 可一键对齐"
                    "（改完不用动游戏里的画质设置）" % (w, h, h / 1080.0))


def check_mcp(report, probes):
    host, port = MCP_HOST, MCP_PORT
    conn, why = probes.tcp_open(host, port)
    if not conn:
        report.warn("%d 连不上（%s）—— 正式挂机前要先双击 start_mcp.bat（本自检不需要它）"
                    % (port, why))
        return
    report.ok("%d 端口可连" % port)
    base = "http://%s:%d" % (host, port)

    status, obj, err = probes.http_json(base + "/health", timeout=1.5)
    if status == 200 and isinstance(obj, dict) and isinstance(obj.get("server"), dict):
        srv = obj["server"]
        report.ok("MCP 身份确认：%s %s | OCR=%s | 设备=%s"
                  % (srv.get("name", "?"), srv.get("version", "?"),
                     obj.get("ocr"), obj.get("ocr_device")))
    else:
        report.warn("%d 被别的程序占用了（不是 sky-mcp：/health %s）—— 先关掉占用方再启动 "
                    "start_mcp.bat" % (port, err or ("HTTP %s" % status)))
        return

    token = (probes.env.get("SKY_MCP_TOKEN") or MCP_TOKEN_DEFAULT).strip()
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "status", "arguments": {}}}
    status2, obj2, err2 = probes.http_json(base, payload=payload, token=token, timeout=3.0)
    if status2 == 401:
        report.warn("MCP token 不匹配（自检用的是 %s）—— start_mcp.bat 的 --token 要和 "
                    "sky-loop-v7.py 里的 MCP_TOKEN 一致" % token)
        return
    if status2 != 200 or not isinstance(obj2, dict):
        report.warn("MCP status 调用失败：%s" % (err2 or ("HTTP %s" % status2)))
        return

    text = ""
    for item in ((obj2.get("result") or {}).get("content") or []):
        if isinstance(item, dict) and item.get("type") == "text":
            text += item.get("text", "")
    try:
        info = json.loads(text)
    except Exception:
        info = None
    if not isinstance(info, dict) or not info:
        report.warn("MCP status 返回无法解析：%s" % (text[:80] or "<空>"))
        return
    report.ok("MCP 正常：按键后端=%s | OCR=%s"
              % (info.get("input_backend"), info.get("ocr")))
    win = info.get("window")
    if isinstance(win, dict) and win:
        report.ok("MCP 看到的窗口：%sx%s @(%s,%s)"
                  % (win.get("width"), win.get("height"), win.get("left"), win.get("top")))
    else:
        report.warn("MCP 看不到光遇窗口 —— 游戏没开或标题不匹配（按键前会报 'Sky window not found'）")


CONFIG_KEYS = (
    ("SKY_LLM_PROVIDER", "openrouter"),
    ("SKY_LLM_MODEL", "代码默认"),
    ("SKY_INPUT_BACKEND", "arduino"),
    ("SKY_SERIAL_PORT", "留空=自动探测"),
    ("SKY_MEM_READER", "0=OCR截图"),
    ("SKY_WHITELIST_ENABLED", "1"),
    ("SKY_WHITELIST", "珂珂"),
    ("SKY_YOLO_ENABLED", "1"),
    ("SKY_SEARCH_ENABLED", "1"),
    ("SKY_AI_TRACE", "0=不写对话转储"),
    ("SKY_VISION_ENABLED", "0=不加云端视觉校验"),
    ("SKY_SEND_KEEP_PANEL", "1=面板开着直接发"),
    ("SKY_CHAT_OCR_SCALE", "1=聊天区不放大"),
    ("SKY_LOG", "1=写启动日志"),
)


def check_config(report, probes):
    report.out("  （未设 = 走代码默认；用 start_loop*.bat 启动时以 bat 里写的为准）")
    for key, default in CONFIG_KEYS:
        report.out("  %-22s = %s" % (key, probes.env.get(key, "<未设, 默认 " + default + ">")))

    if probes.env.get("SKY_MEM_READER") == "1":
        report.warn("内存读取模式：必须管理员权限；网易易盾会识别内存扫描，有封号风险（只建议测试号）；"
                    "该模式下 OCR 线程不启动")

    wl = probes.env.get("SKY_WHITELIST")
    if wl is not None:
        names = [n.strip() for n in wl.split(",") if n.strip()]
        if not names:
            report.warn("SKY_WHITELIST 为空 —— 白名单开着时谁的消息都不会触发 AI 回复")
        elif looks_like_mojibake(wl):
            report.warn("SKY_WHITELIST 疑似乱码（'%s'）—— bat 编码不统一时 cmd 会按 ANSI 读，"
                        "把那个 bat 存成 GBK+CRLF（已知坑，见 PROJECT_HANDOFF 续13）" % wl)

    if probes.env.get("SKY_INPUT_BACKEND") == "pydirectinput":
        report.warn("按键后端 pydirectinput：光遇走 Raw Input，软件模拟按键可能不生效"
                    "（有 Arduino 就用 arduino）")


def _compile_one(report, probes, rel):
    exists, _size = probes.stat(probes.path(rel))
    if not exists:
        report.err("缺少 %s —— 主程序 import 会直接失败" % rel)
        return
    try:
        probes.compile_source(probes.path(rel))
        report.ok("%s 语法 OK" % rel)
    except SyntaxError as e:
        report.err("%s 语法错误：%s" % (rel, e))
    except Exception as e:
        report.err("%s 读取/编译失败：%s: %s" % (rel, type(e).__name__, e))


def check_sources(report, probes):
    for f in SOURCE_FILES:
        _compile_one(report, probes, f)
    for f in CORE_FILES:
        _compile_one(report, probes, os.path.join("core", f))


# ===================== 调度与汇总 =====================

CHECKS = (
    ("1. Python", check_python),
    ("2. 依赖包", check_deps),
    ("3. 关键文件", check_files),
    ("4. Arduino 硬件串口", check_serial),
    ("5. 游戏窗口", check_window),
    ("6. MCP 服务（127.0.0.1:9900）", check_mcp),
    ("7. 当前配置（环境变量，未设则用代码默认）", check_config),
    ("8. 核心源码语法编译", check_sources),
)


def _guard(report, title, fn, probes):
    """跑一项检查：任何未捕获异常都变成一条"自检自身异常"，其余检查照跑。"""
    try:
        fn(report, probes)
    except Exception as e:
        report.crash("%s 自检异常：%s: %s" % (title, type(e).__name__, e))


def run_all(report, probes):
    """按固定顺序跑完全部检查。返回 report。"""
    for title, fn in CHECKS:
        report.section(title)
        _guard(report, title, fn, probes)
    return report


def finish(report, strict=False):
    """打印汇总并返回退出码：0=通过 / 1=有错误（或 strict 下的警告）/ 2=自检自身异常。"""
    report.section("汇总")
    report.out("  通过 %d 项 | 警告 %d 项 | 错误 %d 项"
               % (len(report.oks), len(report.warns), len(report.errors)))
    if report.warns:
        report.out("  -- 警告（可运行，但建议看一眼）--")
        for w in report.warns:
            report.out("   * " + w)
    if report.errors:
        report.out("  -- 错误（不处理无法正常挂机）--")
        for e in report.errors:
            report.out("   X " + e)
    if report.crashed:
        report.out("\n  结论：自检自身异常（上面那几项没跑完，结果不完整），要修 doctor.py。")
        return 2
    if report.errors:
        report.out("\n  结论：存在必须处理的错误。")
        return 1
    if strict and report.warns:
        report.out("\n  结论：--strict 模式下警告也算失败。")
        return 1
    report.out("\n  结论：没有阻断性错误，可以启动。"
               + ("（有 %d 条降级提示）" % len(report.warns) if report.warns else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="光遇 AI 伙伴 环境自检（只读，不改任何东西）")
    ap.add_argument("--strict", action="store_true",
                    help="把 WARN 也当成失败（挂机前严格模式，退出码 1）")
    ap.add_argument("--root", default=ROOT,
                    help="项目根目录（默认 doctor.py 所在目录）")
    args = ap.parse_args(argv)

    report = Report()
    probes = Probes(root=args.root)
    try:
        run_all(report, probes)
    except Exception as e:   # 兜底：自检自己绝不允许带着 traceback 退出
        report.err("自检整体异常：%s: %s" % (type(e).__name__, e))
    return finish(report, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
