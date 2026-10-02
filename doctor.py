# -*- coding: utf-8 -*-
"""
doctor.py — 光遇 AI 伙伴 环境自检（只读，不改任何东西）

在学校/家里换电脑、或者跑不起来时，先执行：  py doctor.py
逐项检查：Python 版本 / 依赖 / 关键文件 / Arduino 串口 / MCP 端口 / 配置，
并区分 ERROR（不处理跑不起来）和 WARN（能跑但会降级或要注意）。
"""
import os
import sys
import importlib
import socket
import py_compile

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

ERRORS, WARNS, OKS = [], [], []


def ok(m):
    OKS.append(m); print("  [ OK ]", m)


def warn(m):
    WARNS.append(m); print("  [WARN]", m)


def err(m):
    ERRORS.append(m); print("  [ERR ]", m)


def section(t):
    print("\n" + "=" * 58 + "\n" + t + "\n" + "=" * 58)


# 1) Python 版本（代码用了 X | None 语法，需要 3.10+）
section("1. Python")
vi = sys.version_info
print("  当前 Python %d.%d.%d" % (vi.major, vi.minor, vi.micro))
if (vi.major, vi.minor) >= (3, 10):
    ok("版本 >= 3.10，语法兼容")
else:
    err("Python 版本过低，需要 3.10 及以上（代码用了 X | None 类型语法）")

# 2) 依赖
section("2. 依赖包")
REQUIRED = {
    "numpy": "numpy", "cv2": "opencv-python", "mss": "mss",
    "rapidocr_onnxruntime": "rapidocr_onnxruntime", "openai": "openai",
    "requests": "requests", "serial": "pyserial", "pyperclip": "pyperclip",
    "pyautogui": "pyautogui", "pygetwindow": "PyGetWindow",
    "win32api": "pywin32", "pydirectinput": "pydirectinput",
}
OPTIONAL = {"ultralytics": "ultralytics（YOLO，缺了退回模板匹配）",
            "torch": "torch（ultralytics 的推理后端）"}
for mod, pipname in REQUIRED.items():
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, "__version__", "")
        ok(f"{pipname:24s} {ver}")
    except Exception as e:
        err(f"{pipname:24s} 未安装/导入失败：{e}  -> py -m pip install {pipname}")
for mod, desc in OPTIONAL.items():
    try:
        m = importlib.import_module(mod)
        ok(f"{desc} {getattr(m, '__version__', '')}")
    except Exception:
        warn(f"{desc} —— 未安装；YOLO 会退回模板匹配（要 YOLO 就 py -m pip install ultralytics）")

# 3) 关键文件
section("3. 关键文件")
def check_file(rel, required=True, hint=""):
    p = os.path.join(ROOT, rel)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        ok(f"{rel}  存在（{os.path.getsize(p)} 字节）")
    elif required:
        err(f"缺少 {rel} {hint}")
    else:
        warn(f"缺少 {rel} {hint}")

if os.environ.get("OPENROUTER_API_KEY"):
    ok("LLM Key 来自环境变量 OPENROUTER_API_KEY")
else:
    check_file("key.txt", required=False,
               hint="（没有它也没设 OPENROUTER_API_KEY 时，LLM 无法鉴权）")
    if not os.path.exists(os.path.join(ROOT, "key.txt")):
        err("既没有 key.txt 也没有环境变量 OPENROUTER_API_KEY，AI 回复会失败")
check_file("persona.txt", hint="（把 persona.example.txt 复制成 persona.txt 并填写人设；缺了主程序直接退出）")
check_file(os.path.join("runs", "detect", "runs", "detect", "train", "weights", "best.pt"),
           required=False, hint="（YOLO 模型；缺了用模板匹配，回家记得放 8-31 负样本版）")
check_file("yolov8n.pt", required=False, hint="（YOLO 训练基座，只在重训时需要）")

# 4) Arduino 串口
section("4. Arduino 硬件串口")
try:
    from serial.tools import list_ports
    ps = list(list_ports.comports())
    known = {0x2341, 0x1B4F, 0x239A}
    hit = None
    for p in ps:
        tag = []
        if p.vid in known or "arduino" in (p.description or "").lower():
            hit = p.device; tag.append("<= 命中 Arduino")
        if p.vid == 0x1A86:
            tag.append("CH340")
        print(f"  {p.device:6s} VID={hex(p.vid) if p.vid else '-':>8s}  {p.description} {' '.join(tag)}")
    if hit:
        ok(f"自动探测将选中 {hit}")
    else:
        ch = [p for p in ps if p.vid == 0x1A86]
        if len(ch) == 1:
            ok(f"单个 CH340，将按山寨 Pro Micro 选中 {ch[0].device}")
        else:
            warn("没探测到 Arduino（没插不影响启动，但硬件按键不可用，会退回软件模拟）")
except Exception as e:
    warn("串口枚举失败：%s" % e)

# 5) MCP 端口
section("5. MCP 服务（127.0.0.1:9900）")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.6)
try:
    s.connect(("127.0.0.1", 9900)); ok("9900 端口可连，MCP 已在运行")
except Exception:
    warn("9900 连不上 —— 正式挂机前要先双击 start_mcp.bat（本自检不需要它）")
finally:
    s.close()

# 6) 配置回显
section("6. 当前配置（环境变量，未设则用代码默认）")
for key, default in [
    ("SKY_LLM_PROVIDER", "openrouter"), ("SKY_LLM_MODEL", "代码默认"),
    ("SKY_INPUT_BACKEND", "arduino"), ("SKY_MEM_READER", "0=OCR截图"),
    ("SKY_WHITELIST_ENABLED", "1"), ("SKY_WHITELIST", "珂珂"),
    ("SKY_YOLO_ENABLED", "1"), ("SKY_LOG", "1=写启动日志")]:
    print(f"  {key:22s} = {os.environ.get(key, '<未设, 默认 ' + default + '>')}")

# 7) 核心源码语法编译
section("7. 核心源码语法编译")
for f in ("sky-loop-v7.py", "sky-mcp-server.py", "panel_detector.py", "mem_reader.py"):
    try:
        py_compile.compile(os.path.join(ROOT, f), doraise=True)
        ok(f"{f} 语法 OK")
    except Exception as e:
        err(f"{f} 语法错误：{e}")

# 汇总
section("汇总")
print(f"  通过 {len(OKS)} 项 | 警告 {len(WARNS)} 项 | 错误 {len(ERRORS)} 项")
if WARNS:
    print("  -- 警告（可运行，但建议看一眼）--")
    for w in WARNS:
        print("   *", w)
if ERRORS:
    print("  -- 错误（不处理无法正常挂机）--")
    for e in ERRORS:
        print("   X", e)
    print("\n  结论：存在必须处理的错误。")
    sys.exit(1)
print("\n  结论：没有阻断性错误，可以启动。" +
          ("（有 %d 条降级提示）" % len(WARNS) if WARNS else ""))
sys.exit(0)
