"""离线单测 doctor.py 的检查逻辑。

不碰真实环境：文件/依赖/串口/窗口/端口全部走注入的 FakeProbes，结果确定、跑得快。
只有最后一个用例真的调用一次 doctor.main()，验证 CLI 端到端不炸（会真实 import 依赖，
约几秒）。doctor.py 本身只读、不写文件，所以这里也不产生副作用。

运行：py test_doctor.py
"""
import importlib.util
import io
import os
import shutil
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("doctor", os.path.join(HERE, "doctor.py"))
doc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doc)


# ===================== 测试替身 =====================

GOOD_KEY = "sk-" + "A" * 40
GOOD_WINDOW = {"title": "光·遇", "left": 100, "top": 50, "width": 1920, "height": 1080}


def _norm(p):
    return str(p).replace("\\", "/")


def green_modules():
    """一个依赖齐全的环境：必需 + 可选 + 截屏后端都有。"""
    mods = {m: "1.0" for m in doc.REQUIRED}
    mods.update({m: "1.0" for m in doc.OPTIONAL})
    mods["bettercam"] = "1.0"
    return mods


def green_files():
    """一个关键文件齐全的环境（YOLO 模型有合法 zip 头）。"""
    files = {
        "persona.txt": b"persona" * 20,
        "key.txt": GOOD_KEY.encode("utf-8"),
        doc.MODEL_REL: b"PK\x03\x04" + b"\x00" * (6 * 1024 * 1024),
        "yolov8n.pt": b"PK\x03\x04" + b"\x00" * 4096,
        os.path.join("user_data", "memory.json"): b"{}",
    }
    for f in doc.SOURCE_FILES:
        files[f] = b"pass\n"
    for f in doc.CORE_FILES:
        files[os.path.join("core", f)] = b"pass\n"
    return files


def green_dirs():
    return {os.path.join("templates", d): ["x.png"] for d in doc.TEMPLATE_DIRS}


def green_http(url, payload, token):
    """MCP 全绿：/health 报身份，tools/call status 报后端和窗口。"""
    if url.endswith("/health"):
        return 200, {"status": "ok", "ocr": "rapidocr", "ocr_device": "cpu",
                     "server": {"name": "sky-mcp-server", "version": "0.2.0"}}, ""
    body = ('{"input_backend": "arduino", "ocr": "rapidocr", '
            '"window": {"left": 1, "top": 2, "width": 1920, "height": 1080}}')
    return 200, {"jsonrpc": "2.0", "id": 1,
                 "result": {"content": [{"type": "text", "text": body}]}}, ""


class FakeProbes(doc.Probes):
    """把 doctor 碰环境的所有探针换成可控数据；默认值 = 一个全绿环境。"""

    def __init__(self, root="X:/proj", env=None, python=(3, 13, 3, 64, "win32"),
                 modules=None, files=None, dirs=None, ports=None, window=GOOD_WINDOW,
                 tcp=(True, ""), http=None, bad_compile=(), io_error=(),
                 ports_error=None):
        super().__init__(root=root, env=env if env is not None else {})
        self._py = python
        self._modules = green_modules() if modules is None else modules
        # 路径键一律归一到 "/"，和 _rel() 的输出对齐（Windows 上 os.path.join 是 "\"）
        self._files = {_norm(k): v for k, v in
                       (green_files() if files is None else files).items()}
        self._dirs = {_norm(k): v for k, v in
                      (green_dirs() if dirs is None else dirs).items()}
        self._ports = ([{"device": "COM5", "vid": 0x2341,
                         "description": "Arduino Leonardo"}] if ports is None else ports)
        self._window = window
        self._tcp = tcp
        self._http = http or green_http
        self._bad_compile = set(_norm(p) for p in bad_compile)
        self._io_error = set(_norm(p) for p in io_error)
        self._ports_error = ports_error

    # --- 覆盖 Probes 的所有外部探针 ---
    def python_info(self):
        major, minor, micro, bits, platform = self._py
        return {"major": major, "minor": minor, "micro": micro, "bits": bits,
                "platform": platform, "impl": "cpython", "exe": "py"}

    def import_module(self, name):
        if name in self._modules:
            return self._modules[name]
        raise ImportError("No module named '%s'" % name)

    def _rel(self, path):
        p = _norm(path)
        root = _norm(self.root).rstrip("/")
        if root and p.startswith(root + "/"):
            return p[len(root) + 1:]
        return p

    def stat(self, path):
        rel = self._rel(path)
        if rel in self._io_error:
            raise OSError("模拟：读不到 %s" % rel)
        data = self._files.get(rel)
        if data is None:
            return False, 0
        return True, len(data)

    def read_bytes(self, path, limit=1 << 20):
        return (self._files.get(self._rel(path)) or b"")[:limit]

    def read_text(self, path, limit=4096):
        data = self._files.get(self._rel(path)) or b""
        return data.decode("utf-8", "replace")[:limit]

    def list_dir(self, path):
        return list(self._dirs.get(self._rel(path), []))

    def compile_source(self, path):
        rel = self._rel(path)
        if rel in self._bad_compile:
            raise SyntaxError("模拟语法错误")
        return len(self._files.get(rel) or b"")

    def list_ports(self):
        if self._ports_error:
            raise RuntimeError(self._ports_error)
        return list(self._ports)

    def find_game_window(self):
        return self._window

    def tcp_open(self, host, port, timeout=0.6):
        return self._tcp

    def http_json(self, url, payload=None, token=None, timeout=3.0):
        return self._http(url, payload, token)


# ===================== 断言小工具 =====================

def run(probes, strict=False):
    """跑完整自检，返回 (report, 全部输出, 退出码)。"""
    buf = []
    rep = doc.Report(out=buf.append)
    doc.run_all(rep, probes)
    rc = doc.finish(rep, strict=strict)
    return rep, "\n".join(buf), rc


def has_ok(rep, needle):
    return any(needle in m for m in rep.oks)


def has_warn(rep, needle):
    return any(needle in m for m in rep.warns)


def has_err(rep, needle):
    return any(needle in m for m in rep.errors)


def expect(desc, cond, extra=""):
    assert cond, "%s -> 失败%s" % (desc, ("；" + extra) if extra else "")
    print("  [PASS] " + desc)


# ===================== 1. 全绿基线 =====================

print("\n[1] 全绿基线")
rep, text, rc = run(FakeProbes())
if rep.errors or rep.warns or rep.crashed:
    print("     错误:", rep.errors)
    print("     警告:", rep.warns)
expect("全绿环境：0 错误 / 0 警告 / 0 自检异常",
       not rep.errors and not rep.warns and not rep.crashed)
expect("全绿环境：退出码 0", rc == 0)
expect("全绿环境：8 个检查段 + 汇总全部打印",
       all(t in text for t, _fn in doc.CHECKS) and "汇总" in text,
       "缺段：%s" % [t for t, _fn in doc.CHECKS if t not in text])
expect("全绿环境：汇总里结论是可启动", "结论：没有阻断性错误，可以启动" in text)


# ===================== 2. Python 版本 =====================

print("\n[2] Python 版本 / 位数")
rep, _, rc = run(FakeProbes(python=(3, 9, 0, 64, "win32")))
expect("Python 3.9 -> 错误并要求 3.10+", has_err(rep, "3.10"))
expect("Python 3.9 -> 退出码 1", rc == 1)

rep, _, _ = run(FakeProbes(python=(3, 13, 3, 32, "win32")))
expect("32 位 Python -> 只警告不报错", has_warn(rep, "64 位") and not rep.errors)

rep, _, _ = run(FakeProbes(python=(3, 10, 0, 64, "win32")))
expect("Python 3.10 边界 -> 通过", has_ok(rep, ">= 3.10") and not rep.errors)


# ===================== 3. 依赖 =====================

print("\n[3] 依赖包")
mods = green_modules()
mods.pop("cv2")
rep, _, rc = run(FakeProbes(modules=mods))
expect("缺 opencv -> 错误并给出 pip 命令",
       has_err(rep, "opencv-python") and has_err(rep, "pip install opencv-python"))
expect("缺必需依赖 -> 退出码 1", rc == 1)

mods = green_modules()
mods.pop("ultralytics")
mods.pop("torch")
rep, _, _ = run(FakeProbes(modules=mods))
expect("缺 ultralytics -> 只警告（降级模板匹配）",
       has_warn(rep, "ultralytics") and not rep.errors)

mods = green_modules()
mods.pop("bettercam")
rep, _, _ = run(FakeProbes(modules=mods))
expect("bettercam/dxcam 都没有 -> 只报一条截屏后端警告",
       sum(1 for w in rep.warns if "截屏" in w) == 1)

mods = green_modules()
mods["dxcam"] = "1.0"
rep, _, _ = run(FakeProbes(modules=mods))
expect("有 DXGI 截屏后端 -> 通过", has_ok(rep, "截屏后端"))


# ===================== 4. key.txt =====================

print("\n[4] key.txt 与鉴权")
files = green_files()
files.pop("key.txt")
rep, _, rc = run(FakeProbes(files=files))
expect("缺 key.txt 且无环境变量 -> 错误", has_err(rep, "key.txt") and rc == 1)

rep, _, _ = run(FakeProbes(files=files, env={"OPENROUTER_API_KEY": "sk-" + "B" * 30}))
expect("有 OPENROUTER_API_KEY 环境变量 -> 不再报错", not rep.errors)

files = green_files()
files["key.txt"] = b"\n   \n"
rep, _, _ = run(FakeProbes(files=files))
expect("key.txt 只有空白 -> 判为空文件", has_err(rep, "空文件"))

files = green_files()
files["key.txt"] = "sk-你的key".encode("utf-8")
rep, _, _ = run(FakeProbes(files=files))
expect("key.txt 还是占位符 -> 错误", has_err(rep, "占位符"))

files = green_files()
files["key.txt"] = b"sk-SECRETSECRETSECRETSECRET1234"
rep, text, _ = run(FakeProbes(files=files))
expect("key.txt 正常 -> 通过", has_ok(rep, "key.txt 存在"))
expect("key 正文绝不出现在自检输出里", "SECRET" not in text)


# ===================== 5. YOLO 模型 =====================

print("\n[5] YOLO 模型文件")
files = green_files()
files.pop(doc.MODEL_REL)
rep, _, _ = run(FakeProbes(files=files))
expect("缺模型 -> 警告（退回模板匹配）不报错",
       has_warn(rep, "退回模板匹配") and not rep.errors)

rep, _, _ = run(FakeProbes(files=files, env={"SKY_YOLO_ENABLED": "0"}))
expect("SKY_YOLO_ENABLED=0 时缺模型不算问题",
       not any("best.pt" in w for w in rep.warns) and not rep.errors)

files = green_files()
files[doc.MODEL_REL] = b"PK\x03\x04" + b"\x00" * 1000
rep, _, _ = run(FakeProbes(files=files))
expect("模型只有 1KB -> 判为坏模型（错误）", has_err(rep, "坏模型"))

files = green_files()
files[doc.MODEL_REL] = b"NOPE" + b"\x00" * (6 * 1024 * 1024)
rep, _, _ = run(FakeProbes(files=files))
expect("模型头不是 zip -> 警告可能损坏", has_warn(rep, "torch"))


# ===================== 6. Arduino 串口 =====================

print("\n[6] Arduino 串口")
rep, _, _ = run(FakeProbes())
expect("命中 Arduino VID -> 通过并给出串口名", has_ok(rep, "COM5"))

rep, _, _ = run(FakeProbes(ports=[
    {"device": "COM7", "vid": 0x1A86, "description": "USB-SERIAL CH340"},
    {"device": "COM8", "vid": 0x1A86, "description": "USB-SERIAL CH340"}]))
expect("两个 CH340 -> 警告（不敢自动选），不报错",
       has_warn(rep, "没探测到 Arduino") and not rep.errors)

rep, _, _ = run(FakeProbes(ports=[]))
expect("一个串口都没有 -> 警告并给出无硬件启动脚本",
       has_warn(rep, "start_loop_no_hardware"))

rep, _, _ = run(FakeProbes(ports_error="pyserial 未安装"))
expect("串口枚举抛异常 -> 降级成警告，不算自检异常",
       has_warn(rep, "串口枚举失败") and not rep.crashed)

rep, _, _ = run(FakeProbes(env={"SKY_SERIAL_PORT": "COM9"}))
expect("SKY_SERIAL_PORT 与实物不符 -> 警告", has_warn(rep, "COM9"))

rep, _, _ = run(FakeProbes(env={"SKY_SERIAL_PORT": "COM5"}))
expect("SKY_SERIAL_PORT 与实物一致 -> 通过", has_ok(rep, "COM5 已插着"))


# ===================== 7. 游戏窗口 =====================

print("\n[7] 游戏窗口")
rep, _, rc = run(FakeProbes(window=None))
expect("找不到游戏窗口 -> 警告但退出码仍为 0",
       has_warn(rep, "没找到光遇窗口") and rc == 0 and not rep.errors)

rep, _, _ = run(FakeProbes(window={"title": "光·遇", "left": 0, "top": 0,
                                   "width": 1600, "height": 900}))
expect("窗口正常 -> 通过并回显尺寸", has_ok(rep, "1600x900"))
expect("1600x900 不是 1920x1080 -> 提示可用 window_fix.py 对齐",
       has_warn(rep, "window_fix") and not rep.errors)

rep, _, _ = run(FakeProbes(window=GOOD_WINDOW))
expect("正好 1920x1080 -> 不提示分辨率",
       not has_warn(rep, "window_fix") and not has_warn(rep, "不是标定"))

rep, _, _ = run(FakeProbes(window={"title": "光·遇", "left": 0, "top": 0,
                                   "width": 120, "height": 90}))
expect("窗口小到异常（最小化）-> 错误", has_err(rep, "拒绝启动"))

rep, _, _ = run(FakeProbes(python=(3, 13, 3, 64, "linux"), window=None))
expect("非 Windows -> 跳过窗口检查（警告不报错）",
       has_warn(rep, "非 Windows") and not rep.errors)


# ===================== 8. MCP 端口 =====================

print("\n[8] MCP 服务 9900")
rep, _, rc = run(FakeProbes(tcp=(False, "ConnectionRefusedError")))
expect("9900 连不上 -> 警告 + 提示 start_mcp.bat，退出码 0",
       has_warn(rep, "start_mcp.bat") and rc == 0)

rep, _, _ = run(FakeProbes(http=lambda u, p, t: (200, {"hi": 1}, "")))
expect("9900 被别的程序占用 -> 警告（不是 sky-mcp）", has_warn(rep, "被别的程序占用"))


def _http_401_on_status(url, payload, token):
    if url.endswith("/health"):
        return 200, {"server": {"name": "sky-mcp-server", "version": "0.2.0"},
                     "ocr": "rapidocr"}, ""
    return 401, None, "HTTP 401"


rep, _, _ = run(FakeProbes(http=_http_401_on_status))
expect("MCP token 不匹配 -> 警告", has_warn(rep, "token 不匹配"))


def _http_no_window(url, payload, token):
    if url.endswith("/health"):
        return 200, {"server": {"name": "sky-mcp-server", "version": "0.2.0"},
                     "ocr": "rapidocr"}, ""
    body = '{"input_backend": "arduino", "window": null}'
    return 200, {"result": {"content": [{"type": "text", "text": body}]}}, ""


rep, _, _ = run(FakeProbes(http=_http_no_window))
expect("MCP 活着但看不到窗口 -> 警告提示 Sky window not found",
       has_warn(rep, "Sky window not found"))

rep, _, _ = run(FakeProbes())
expect("MCP 全绿 -> 报出身份和按键后端",
       has_ok(rep, "sky-mcp-server") and has_ok(rep, "按键后端=arduino"))


# ===================== 9. 配置提示（含 bat 编码坑） =====================

print("\n[9] 配置回显与提示")
expect("GBK 误读的 UTF-8 白名单能被识别成乱码",
       doc.looks_like_mojibake("鐝傜弬,骞哄购"))
expect("正常中文白名单不误判", not doc.looks_like_mojibake("珂珂,幺幺,阿颜"))
expect("ASCII 名字不误判", not doc.looks_like_mojibake("Aki"))

rep, _, _ = run(FakeProbes(env={"SKY_WHITELIST": "鐝傜弬"}))
expect("白名单疑似乱码 -> 警告", has_warn(rep, "乱码"))

rep, _, _ = run(FakeProbes(env={"SKY_WHITELIST": ","}))
expect("白名单为空 -> 警告", has_warn(rep, "为空"))

rep, _, _ = run(FakeProbes(env={"SKY_MEM_READER": "1"}))
expect("内存读取模式 -> 警告封号风险", has_warn(rep, "封号风险"))

rep, _, _ = run(FakeProbes(env={"SKY_INPUT_BACKEND": "pydirectinput"}))
expect("软件按键后端 -> 警告可能不生效", has_warn(rep, "Raw Input"))

rep, text, _ = run(FakeProbes(env={"SKY_WHITELIST": "珂珂"}))
expect("未设的配置项回显为默认值", "<未设, 默认 openrouter>" in text)


# ===================== 10. 源码语法 =====================

print("\n[10] 源码语法检查")
rep, _, rc = run(FakeProbes(bad_compile=["sky-loop-v7.py"]))
expect("语法错误 -> 记为错误而不是自检异常",
       has_err(rep, "语法错误") and not rep.crashed and rc == 1)

files = green_files()
files.pop(os.path.join("core", "memory.py"))
rep, _, _ = run(FakeProbes(files=files))
expect("core 模块缺失 -> 明确报缺文件", has_err(rep, "memory.py"))

rep, _, _ = run(FakeProbes())
expect("10 个源码文件全部检查到",
       len([m for m in rep.oks if m.endswith("语法 OK")])
       == len(doc.SOURCE_FILES) + len(doc.CORE_FILES))


# ===================== 11. 单项独立 / 异常不崩溃 =====================

print("\n[11] 单项独立：一项崩了不影响其它项")
rep, text, rc = run(FakeProbes(io_error=["persona.txt"]))
expect("检查内抛异常 -> 记成「自检自身异常」", len(rep.crashed) == 1)
expect("有自检异常 -> 退出码 2", rc == 2)
expect("崩溃后其余检查照跑完（第 8 段仍在）",
       "8. 核心源码语法编译" in text and has_ok(rep, "sky-loop-v7.py 语法 OK"))
expect("崩溃也写进汇总", "自检自身异常" in text)

rep, _, rc = run(FakeProbes(io_error=["persona.txt", "key.txt"]))
expect("崩溃项不重复刷屏（persona 崩在第 3 段，key 检查已完成）",
       len(rep.crashed) == 1 and rc == 2)


# ===================== 12. 退出码 =====================

print("\n[12] 退出码语义")
quiet = doc.Report(out=lambda s: None)
quiet.ok("ok")
expect("只有通过 -> 0", doc.finish(quiet) == 0)
quiet.warn("w")
expect("只有警告 -> 0", doc.finish(quiet) == 0)
expect("只有警告 + strict -> 1", doc.finish(quiet, strict=True) == 1)
quiet.err("e")
expect("有错误 -> 1", doc.finish(quiet) == 1)
quiet.crash("boom")
expect("有自检异常 -> 2（优先于 1）", doc.finish(quiet) == 2)
expect("警告也走 strict 语义（strict 不影响错误码优先级）",
       doc.finish(quiet, strict=True) == 2)

rep, _, rc = run(FakeProbes(window=None, tcp=(False, "refused")), strict=True)
expect("--strict 下只有警告 -> 退出码 1", rc == 1 and not rep.errors)


# ===================== 13. CLI 端到端 =====================

print("\n[13] CLI 端到端（真实探针，几秒）")
tmp = tempfile.mkdtemp(prefix="doctor_e2e_")
try:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = doc.main(["--root", tmp])
    out = buf.getvalue()
    expect("空目录端到端 -> 退出码 1（缺文件）", rc == 1)
    expect("端到端不抛 traceback", "Traceback" not in out)
    expect("端到端打印了汇总", "汇总" in out)
    expect("端到端认出了缺 persona.txt", "persona.txt" in out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n全部通过")
