# debug_web.py — sky-with-you 本地 Web 调试台
#
# 用途：不跑 AI 自动决策，手动单独触发每一个操作（单个按键 / 开关面板 /
#       发消息 / 回遇境 / 好友树动作等），并实时看感知状态与日志。
#
# 启动（先开 MCP server，再开本程序）：
#   1. python sky-mcp-server.py --http --port 9900 --token 1234 --serial-port COM5
#   2. python debug_web.py
#   然后浏览器打开 http://127.0.0.1:9911
#
# 实现：用标准库 http.server，不引入 Flask；通过 importlib 加载 sky-loop-v7.py
#       复用其全部动作函数，不改动主程序。
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import queue
import threading
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_PORT = int(os.environ.get("SKY_DEBUG_PORT", "9911"))

# ===================== 日志捕获（Tee 到控制台 + ring buffer） =====================

_logs: list = []
_logs_lock = threading.Lock()
_LOG_MAX = 200


class _Tee:
    def __init__(self, original):
        self.original = original

    def write(self, s):
        try:
            self.original.write(s)
        except Exception:
            pass
        if s and s.strip():
            with _logs_lock:
                _logs.append(s.rstrip())
                if len(_logs) > _LOG_MAX:
                    del _logs[0: len(_logs) - _LOG_MAX]

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass


sys.stdout = _Tee(sys.__stdout__)
sys.stderr = _Tee(sys.__stderr__)


def log(msg):
    print(f"[Debug] {msg}")


# ===================== 加载 sky-loop 主程序模块（不执行 main） =====================

def _load_sky():
    path = os.path.join(HERE, "sky-loop-v7.py")
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}，请把 debug_web.py 放在项目根目录")
    spec = importlib.util.spec_from_file_location("sky_loop", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 顶层代码执行（加载配置/全局变量），但不跑 main()
    return mod


sky = _load_sky()

# ===================== 初始化感知 / 状态 / 会话（仿 main，不启自动线程） =====================

det = None
state = None
sess = None
mcp_ok = False
init_error = ""


def _init():
    global det, state, sess, mcp_ok, init_error
    # 1. MCP 会话（短超时探测，避免连不上时卡 30 秒重试）
    sess = sky._make_session()

    def _quick_status():
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "status", "arguments": {}}}
        r = sess.post(sky.MCP_URL, json=payload, timeout=2.0)
        d = r.json()
        return json.loads("\n".join(
            i["text"] for i in d["result"]["content"]
            if i.get("type") == "text"))

    try:
        st = _quick_status()
        mcp_ok = True
        log(f"MCP 已连接，后端={st.get('input_backend')}")
    except Exception as e:
        init_error = f"MCP 连不上: {e}（先跑 sky-mcp-server.py）"
        log(init_error)

    # 2. 定位游戏窗口
    region = None
    follow = False
    try:
        region = sky.find_game_client_region(sky.DetectorConfig.window_titles)
        if region:
            follow = True
            log(f"窗口(客户区): {region}")
    except Exception:
        region = None
    if region is None and mcp_ok:
        try:
            st = _quick_status()
            win = st.get("window")
            if win:
                region = {"left": win["left"], "top": win["top"],
                          "width": max(1, win["width"]),
                          "height": max(1, win["height"])}
                follow = False
                log(f"窗口(MCP整窗): {region}")
        except Exception:
            pass

    # 3. 启动 PanelDetector
    if region is not None:
        try:
            import cv2
            cv2.setNumThreads(2)
            det = sky.PanelDetector(
                region=region, follow_window=follow,
                cfg=sky.DetectorConfig(debug=False, sense_interval=0.15,
                                       ocr_full_interval=10.0))
            det.start()
            log("PanelDetector 已启动")
        except Exception as e:
            log(f"PanelDetector 启动失败: {e}（高级动作不可用）")
    else:
        log("未找到游戏窗口，高级动作暂不可用（基础 MCP 操作仍可测试）")

    # 4. 加载 YOLO 模型（设置 sky 模块全局变量）
    if sky.YOLO_ENABLED:
        try:
            from ultralytics import YOLO
            if os.path.exists(sky.YOLO_MODEL_PATH):
                sky._yolo_model = YOLO(sky.YOLO_MODEL_PATH)
                log(f"YOLO 模型已加载: {os.path.basename(sky.YOLO_MODEL_PATH)} "
                    f"（置信度阈值 {sky.YOLO_CONF_THRESHOLD}）")
            else:
                log(f"YOLO 模型文件不存在: {sky.YOLO_MODEL_PATH}")
        except ImportError:
            log("ultralytics 未安装，YOLO 不可用")
        except Exception as e:
            log(f"YOLO 加载失败: {e}")

    # 5. 共享状态
    state = sky.SharedState()


_init()


# ===================== 纯感知线程（只更新 YOLO 全局状态，不自动动作） =====================

def _sense_loop():
    """周期性 YOLO 推理，更新 sky 模块全局状态。
    逻辑取自 sky-loop 的 watch_loop，去掉自动触发部分。"""
    last = 0.0
    while True:
        try:
            now = time.time()
            if (sky._yolo_model is not None and det is not None
                    and now - last > sky.YOLO_DETECT_INTERVAL):
                last = now
                frame = det.latest_frame()
                if frame is not None:
                    results = sky._yolo_model(
                        frame, conf=sky.YOLO_CONF_THRESHOLD, verbose=False)
                    found_hand = found_tree = found_chat = False
                    max_hand = max_tree = max_chat = 0.0
                    hand_bbox = None
                    if len(results[0].boxes) > 0:
                        fh, fw = frame.shape[:2]
                        for box in results[0].boxes:
                            c = int(box.cls[0])
                            cf = float(box.conf[0])
                            name = results[0].names[c]
                            x1, y1, x2, y2 = map(float, box.xyxy[0])
                            if name == "hand_icon":
                                found_hand = True
                                if cf > max_hand:
                                    max_hand = cf
                                    hand_bbox = (x1, y1, x2, y2)
                            elif name == "open_tree_icon":
                                found_tree = True
                                max_tree = max(max_tree, cf)
                            elif name == "chat_input":
                                # 位置约束（同 watch_loop）
                                if (x1 < fw * 0.10 and y1 > fh * 0.55
                                        and x2 < fw * 0.50
                                        and y2 <= fh * 1.02):
                                    found_chat = True
                                    max_chat = max(max_chat, cf)
                    sky._yolo_hand_detected = found_hand
                    sky._yolo_hand_conf = max_hand
                    sky._yolo_hand_bbox = hand_bbox
                    sky._yolo_open_tree_detected = found_tree
                    sky._yolo_open_tree_conf = max_tree
                    sky._yolo_chat_input_detected = found_chat
                    sky._yolo_chat_input_conf = max_chat
        except Exception as e:
            log(f"感知线程异常: {e}")
        time.sleep(0.1)


threading.Thread(target=_sense_loop, name="Sense", daemon=True).start()


# ===================== 动作执行（串行，执行前自动聚焦游戏） =====================

_action_q = queue.Queue(maxsize=8)


def _mcp_alive():
    """短超时探测 MCP 是否可连（1.5 秒），避免动作 worker 卡长重试。"""
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "status", "arguments": {}}}
        r = sess.post(sky.MCP_URL, json=payload, timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def _find_game_hwnd():
    """枚举顶层窗口，返回游戏窗口 hwnd（排除控制台）。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    titles = ("光·遇", "Sky: Children of the Light", "光遇", "Sky")
    console_classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
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
                found.append((pri, hwnd))
                break
            if len(t) >= 8 and t in title:
                found.append((pri + 100, hwnd))
                break
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    return found[0][1]


def _force_foreground(hwnd):
    """强制把 hwnd 切到前台（AttachThreadInput 绕过 Windows 前台锁定）。"""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached_fg = False
    attached_tgt = False
    if fg_tid != cur_tid:
        user32.AttachThreadInput(cur_tid, fg_tid, True)
        attached_fg = True
    if tgt_tid != cur_tid and tgt_tid != fg_tid:
        user32.AttachThreadInput(cur_tid, tgt_tid, True)
        attached_tgt = True
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetActiveWindow(hwnd)
    if attached_fg:
        user32.AttachThreadInput(cur_tid, fg_tid, False)
    if attached_tgt:
        user32.AttachThreadInput(cur_tid, tgt_tid, False)


def _focus_game():
    # 本地强制切前台：Arduino 模式下 MCP 设了 SKY_SKIP_FOCUS=1，focus_game 不真正切，
    # 所以必须由调试台自己把游戏切到前台，否则按键会进浏览器而非游戏。
    hwnd = _find_game_hwnd()
    if hwnd:
        try:
            _force_foreground(hwnd)
        except Exception as e:
            log(f"本地切前台异常: {e}")
        time.sleep(0.6)
    else:
        log("没找到游戏窗口，无法切前台")
    # 再通知 MCP（仅在可连时，避免长重试；被 SKY_SKIP_FOCUS 跳过也无害）
    if _mcp_alive():
        try:
            sky.mcp("focus_game", session=sess)
            time.sleep(0.3)
        except Exception as e:
            log(f"MCP focus_game 提示失败（可忽略）: {e}")
    return hwnd is not None


def _execute(action, params):
    # focus 是纯本地切前台，不依赖 MCP
    if action == "focus":
        _focus_game()
        return
    if not _mcp_alive():
        log("MCP 未连接，跳过动作（先开 sky-mcp-server）")
        return
    if det is None and action != "press":
        log(f"无感知器，动作 {action} 可能不可用")
    _focus_game()
    if action == "press":
        key = params.get("key")
        ms = int(params.get("ms", 80))
        sky._key(sess, key, ms)
        log(f"已按键: {key} ({ms}ms)")
    elif action == "open_panel":
        sky._open_panel(det, sess, state)
    elif action == "close_panel":
        sky._close_panel(det, sess)
    elif action == "send_msg":
        text = params.get("text", "")
        if text:
            sky._send_msg(det, sess, text, state)
    elif action == "go_home":
        sky._go_home_action(det, state, sess)
    elif action == "friend_tree":
        act = params.get("act", "抱抱")
        sky._friend_tree_action(det, sess, act)
    elif action == "confirm":
        sky._confirm_action(det, state, sess)
    else:
        log(f"未知动作: {action}")


def _action_worker():
    while True:
        action, params = _action_q.get()
        try:
            _execute(action, params)
        except Exception as e:
            log(f"动作 {action} 异常: {e}")
        finally:
            _action_q.task_done()


threading.Thread(target=_action_worker, name="ActionWorker",
                 daemon=True).start()


# ===================== 状态序列化 =====================

def _ch(c):
    return {"value": c.value, "conf": round(c.confidence, 2),
            "source": c.source}


def get_state():
    data = {"mcp_ok": mcp_ok, "detector": det is not None,
            "world": None, "state": None, "yolo": None,
            "logs": [], "ts": time.time()}
    if det is not None:
        s = det.world.snapshot()
        data["world"] = {
            "screen": s.screen.name,
            "chat_open": _ch(s.chat_open),
            "friend_tree": _ch(s.friend_tree),
            "confirm_dialog": _ch(s.confirm_dialog),
            "f_prompt": _ch(s.f_prompt),
            "f_prompt_name": s.f_prompt_name,
            "confirm_text": s.confirm_text,
            "confirm_keywords": list(s.confirm_keywords),
        }
    if state is not None:
        data["state"] = {
            "is_holding_hands": state.is_holding_hands,
            "pose_state": state.pose_state,
            "gohome_completed": state.gohome_completed,
            "action_busy": state.action_busy,
        }
    data["yolo"] = {
        "model_loaded": sky._yolo_model is not None,
        "hand": {"detected": sky._yolo_hand_detected,
                 "conf": round(sky._yolo_hand_conf, 2)},
        "open_tree": {"detected": sky._yolo_open_tree_detected,
                      "conf": round(sky._yolo_open_tree_conf, 2)},
        "chat_input": {"detected": sky._yolo_chat_input_detected,
                       "conf": round(sky._yolo_chat_input_conf, 2)},
    }
    with _logs_lock:
        data["logs"] = list(_logs)
    return data


def get_frame_jpeg():
    if det is None:
        return None
    import cv2
    frame = det.latest_frame()
    if frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame,
                           [cv2.IMWRITE_JPEG_QUALITY, 65])
    return buf.tobytes() if ok else None


# ===================== HTTP 服务 =====================

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            page = os.path.join(HERE, "webui", "index.html")
            try:
                with open(page, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            except FileNotFoundError:
                self._json({"error": "webui/index.html 缺失"}, 500)
        elif p == "/api/state":
            self._json(get_state())
        elif p == "/api/frame.jpg":
            data = get_frame_jpeg()
            if data:
                self._send(200, "image/jpeg", data)
            else:
                self.send_error(503, "frame unavailable")
        else:
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/api/action":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(length) or "{}")
        except json.JSONDecodeError:
            self._json({"queued": False, "error": "invalid JSON"}, 400)
            return
        action = data.get("action")
        params = data.get("params") or {}
        try:
            _action_q.put_nowait((action, params))
            self._json({"queued": True, "action": action})
        except queue.Full:
            self._json({"queued": False, "error": "动作队列满，请稍后"})

    def log_message(self, fmt, *args):
        pass  # 静默默认访问日志，避免刷屏


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Handler)
    print("=" * 50)
    print("sky-with-you Web 调试台")
    print(f"  地址: http://127.0.0.1:{WEB_PORT}")
    print(f"  MCP: {'已连接' if mcp_ok else '未连接（先开 sky-mcp-server）'}")
    print(f"  感知: {'已启动' if det is not None else '未启动（开游戏）'}")
    print(f"  YOLO: {'已加载' if sky._yolo_model is not None else '未加载'}")
    print("=" * 50)
    print("Ctrl+C 退出")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n退出中...")
        try:
            if det is not None:
                det.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
