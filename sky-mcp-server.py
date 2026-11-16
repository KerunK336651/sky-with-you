#!/usr/bin/env python3
"""
Sky MCP Server for PC Sky.

This server is for the Windows/PC version of Sky running on the same computer.
It exposes MCP tools for keyboard control, chat, screenshots, and OCR.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import importlib
import io
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

# 弹琴模块：曲谱解析 / 键位 / 播放时序，改编自 windhide/SkyMusicPlay-for-Windows
# （CC BY-NC 4.0，已获作者授权，署名见 sky_song_player.py 文件头）。
try:
    import sky_song_player
except Exception as _song_err:  # 缺失时不影响其余按键/聊天工具
    sky_song_player = None
    _SONG_IMPORT_ERROR = str(_song_err)
else:
    _SONG_IMPORT_ERROR = ""

SERVER_INFO = {"name": "sky-mcp-server", "version": "0.3.0-song"}
PROTOCOL_VERSION = "2025-03-26"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# 需要在退出时释放的修饰键（防止键盘卡住）
_MODIFIER_KEYS = [
    "shift", "shiftleft", "shiftright",
    "ctrl", "ctrlleft", "ctrlright",
    "alt", "altleft", "altright",
    "win", "winleft", "winright",
    "capslock", "numlock", "scrolllock",
]


def _release_all_keys():
    """退出时释放所有可能卡住的修饰键。"""
    try:
        import pydirectinput as pdi
        for k in _MODIFIER_KEYS:
            try:
                pdi.keyUp(k)
            except Exception:
                pass
    except Exception:
        pass
    try:
        import pyautogui
        for k in ["shift", "ctrl", "alt", "win", "capslock", "numlock"]:
            try:
                pyautogui.keyUp(k)
            except Exception:
                pass
    except Exception:
        pass
    print("[sky-mcp] cleanup: released all modifier keys")


class SkyError(RuntimeError):
    pass


class HardwareLostError(SkyError):
    """Arduino 硬件未连接 / 中途拔出 / 通信失败。

    与前台安全错误区分：这个错误可以安全地用软件模拟补发当次按键，
    而不是把整个动作中断掉。
    """


_LOG_FILE = None
_LOG_LOCK = threading.Lock()


def _mcp_log_file():
    """懒打开 logs/mcp_时间.txt（和主循环的 loop_*.txt 并列，方便一起回传）。

    为什么要有：MCP 窗口的日志只打在屏幕上（`wing guard <- ACTIVE`、按键拒绝原因都在这），
    窗口一关就没了，出问题时没法复盘。`SKY_MCP_LOG=0` 可关闭，`SKY_LOG_DIR` 可改目录。
    """
    global _LOG_FILE
    if _LOG_FILE is not None:
        return _LOG_FILE
    if os.environ.get("SKY_MCP_LOG", "1") == "0":
        _LOG_FILE = False
        return _LOG_FILE
    try:
        log_dir = os.environ.get("SKY_LOG_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "mcp_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
        _LOG_FILE = open(path, "w", encoding="utf-8", buffering=1)
        # 只留最近 5 份，和主循环 log 的策略一致，别把磁盘堆满
        try:
            olds = sorted(g for g in os.listdir(log_dir)
                          if g.startswith("mcp_") and g.endswith(".txt"))
            for g in olds[:-5]:
                os.remove(os.path.join(log_dir, g))
        except Exception:
            pass
    except Exception:
        _LOG_FILE = False
    return _LOG_FILE


def log(message: str) -> None:
    line = f"[sky-mcp] {message}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    f = _mcp_log_file()
    if f:
        try:
            with _LOG_LOCK:
                f.write(time.strftime("%H:%M:%S ") + line)
        except Exception:
            pass


def load_optional(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 9800
    token: str | None = None
    allow_unsafe_http: bool = False
    window_title: str | None = None
    monitor: int = 1
    input_backend: str = "auto"
    screenshot_scale: float = 1.0
    screenshot_max_width: int = 1920
    screenshot_max_height: int = 1080
    serial_port: str | None = None


class PcSkyController:
    def __init__(self, config: ServerConfig):
        self.config = config
        self._pyautogui = None
        self._pydirectinput = None
        self._mss = None
        self._image_cls = None
        self._pyperclip = None
        self._ocr_name = "none"
        self._ocr_device = "unknown"
        self._ocr_engine = None
        self._gamepad = None
        self._vgamepad = None
        self._arduino = None
        self._serial_lock = threading.Lock()
        # 硬件热插拔自适应：Arduino 在线就用真键盘，拔出/没插自动退回软件模拟
        self._hardware_online = False
        self._hw_lock = threading.Lock()
        self._hw_monitor_stop = threading.Event()
        self._hw_monitor_thread = None
        self._last_software_warn = 0.0
        self._hw_warn_interval = 300.0  # 软件模式下重复提示的节流间隔（秒）
        # 光翼硬门在 MCP 层的镜像状态（2026-11-01）：由 sky-loop 通过 set_wing_guard 推送。
        # 原先硬门只在 sky-loop 的 _key() 里，任何直接调用 MCP press_key("f") 的路径
        # （外部脚本/其它会话/调试工具）都能绕过它。这里把它下沉到真正的输入边界。
        self._wing_guard_active = False
        self._wing_guard_at = 0.0
        self._wing_guard_lock = threading.Lock()
        # 弹琴（sky_song_player）
        self._song_player = None
        self._firmware_version = 2      # attach 时按 READY 横幅更新（v3 才支持 CHORD 真和弦）
        self._us_layout_set = False     # 软件发键前是否已加载过英文键盘布局
        self._chord_soft_fallback_notified = False
        atexit.register(self._stop_song_on_exit)

    def status(self) -> dict[str, Any]:
        with self._hw_lock:
            hw_online = self._hardware_online
        return {
            "mode": "pc",
            "platform": sys.platform,
            "input_backend": self._select_input_backend_name(),
            "input_backend_preferred": self._resolve_input_backend_name(),
            "hardware_online": hw_online,
            "arduino_port": self._probe_arduino_port(),
            "software_fallback": self._software_fallback_name(),
            "ocr": self._detect_ocr_name(),
            "ocr_device": self._ocr_device,
            "wing_guard": self.wing_guard_state(),
            "screenshot_max": {
                "width": self.config.screenshot_max_width,
                "height": self.config.screenshot_max_height,
            },
            "window": self.find_window(),
        }

    def ensure_capture_deps(self) -> None:
        if self._mss is None:
            self._mss = load_optional("mss")
        if self._image_cls is None:
            pil = load_optional("PIL.Image")
            self._image_cls = pil
        if self._mss is None:
            raise SkyError("Missing dependency: mss. Run: pip install -r requirements.txt")
        if self._image_cls is None:
            raise SkyError("Missing dependency: Pillow. Run: pip install -r requirements.txt")

    def _select_input_backend_name(self) -> str:
        return self._effective_backend()

    def _software_fallback_name(self) -> str:
        """软件模拟兜底后端：优先 pydirectinput（SendInput + 硬件扫描码，
        与 SkyAuto 同款，DirectX 游戏更可能收到），否则 pyautogui。"""
        if sys.platform.startswith("win") and load_optional("pydirectinput") is not None:
            return "pydirectinput"
        return "pyautogui"

    def _resolve_input_backend_name(self, backend: str | None = None) -> str:
        """用户/配置表达的"偏好"后端（不考虑硬件是否在线）。

        auto 与 arduino 都表示"硬件优先"：插着板子用 Arduino 真键盘，
        没插/拔出时自动退回软件模拟，由 `_effective_backend()` 做最终裁决。
        """
        backend = (backend or self.config.input_backend or "auto").lower().strip()
        if backend == "auto":
            return "arduino"  # 硬件优先；掉线时 _effective_backend 自动软兜底
        if backend not in {"pyautogui", "pydirectinput", "gamepad", "arduino"}:
            raise SkyError(f"Unsupported input backend: {backend}")
        return backend

    def _effective_backend(self, backend: str | None = None) -> str:
        """本次按键实际使用的后端：Arduino 在线就用硬件，否则退回软件模拟。"""
        preferred = self._resolve_input_backend_name(backend)
        if preferred == "arduino":
            with self._hw_lock:
                online = self._hardware_online
            return "arduino" if online else self._software_fallback_name()
        return preferred

    def _input_module(self, backend: str | None = None):
        backend = self._effective_backend(backend)
        # 硬件/手柄有专用路径，不会进到这里；稳妥起见一律落到软件兜底
        if backend in ("arduino", "gamepad"):
            backend = self._software_fallback_name()
        if backend == "pydirectinput":
            if self._pydirectinput is None:
                self._pydirectinput = load_optional("pydirectinput")
            if self._pydirectinput is None:
                raise SkyError("Missing dependency: pydirectinput. Run: pip install -r requirements.txt")
            self._pydirectinput.PAUSE = 0.01
            return self._pydirectinput
        if self._pyautogui is None:
            self._pyautogui = load_optional("pyautogui")
        if self._pyautogui is None:
            raise SkyError("Missing dependency: pyautogui. Run: pip install -r requirements.txt")
        self._pyautogui.FAILSAFE = False
        self._pyautogui.PAUSE = 0.01
        return self._pyautogui

    def _ensure_gamepad(self):
        if self._gamepad is not None:
            return self._gamepad
        if self._vgamepad is None:
            self._vgamepad = load_optional("vgamepad")
        if self._vgamepad is None:
            raise SkyError("Missing dependency: vgamepad. Run: pip install vgamepad")
        self._gamepad = self._vgamepad.VX360Gamepad()
        log("Virtual Xbox 360 gamepad connected (persistent)")
        return self._gamepad

    GAMEPAD_MAP = {
        "w":     ("stick", (0.0, 1.0)),
        "s":     ("stick", (0.0, -1.0)),
        "a":     ("stick", (-1.0, 0.0)),
        "d":     ("stick", (1.0, 0.0)),
        "space": ("btn", "XUSB_GAMEPAD_A"),
        "f":     ("btn", "XUSB_GAMEPAD_X"),
        "q":     ("btn", "XUSB_GAMEPAD_B"),
        "tab":   ("btn", "XUSB_GAMEPAD_Y"),
        "e":     ("btn", "XUSB_GAMEPAD_RIGHT_SHOULDER"),
        "shift": ("btn", "XUSB_GAMEPAD_LEFT_SHOULDER"),
        "shift-space": ("btn_combo", ("XUSB_GAMEPAD_LEFT_SHOULDER", "XUSB_GAMEPAD_A")),
    }

    def _gamepad_press(self, key: str, duration_ms: int = 80) -> str:
        pad = self._ensure_gamepad()
        vg = self._vgamepad
        mapping = self.GAMEPAD_MAP.get(key)
        if mapping is None:
            raise SkyError(f"No gamepad mapping for key: {key}. Available: {list(self.GAMEPAD_MAP.keys())}")
        duration = max(0, int(duration_ms)) / 1000
        kind = mapping[0]
        value = mapping[1]

        if kind == "stick":
            x, y = value
            pad.left_joystick_float(x_value_float=x, y_value_float=y)
            pad.update()
            time.sleep(duration)
            pad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
            pad.update()
        elif kind == "btn":
            btn = getattr(vg.XUSB_BUTTON, value, None)
            if btn is None:
                raise SkyError(f"Unknown gamepad button: {value}")
            pad.press_button(button=btn)
            pad.update()
            time.sleep(duration)
            pad.release_button(button=btn)
            pad.update()
        elif kind == "btn_combo":
            btns = [getattr(vg.XUSB_BUTTON, b) for b in value]
            for b in btns:
                pad.press_button(button=b)
            pad.update()
            time.sleep(duration)
            for b in btns:
                pad.release_button(button=b)
            pad.update()

        return f"gamepad {key} for {int(duration_ms)}ms"

    # Arduino 官方及常见开源控制板 USB VID（Pro Micro ATmega32U4 原生为 0x2341）
    ARDUINO_KNOWN_VIDS = {0x2341, 0x1B4F, 0x239A}
    CH340_VID = 0x1A86  # 山寨 Pro Micro 常用 CH340 串口芯片

    def _autodetect_arduino_port(self):
        """未显式指定串口时自动扫描 Arduino，换电脑 COM 号变化也免配置。"""
        try:
            from serial.tools import list_ports
        except Exception:
            return None
        try:
            ports = list(list_ports.comports())
        except Exception:
            return None
        # 1) 已知 Arduino/开源硬件 VID，直接命中
        for p in ports:
            if p.vid in self.ARDUINO_KNOWN_VIDS:
                return p.device
        # 2) 描述字符串里带 Arduino 的设备
        for p in ports:
            if "arduino" in (p.description or "").lower():
                return p.device
        # 3) 只有一个 CH340 串口时，按山寨 Pro Micro 处理
        ch340 = [p for p in ports if p.vid == self.CH340_VID]
        if len(ch340) == 1:
            return ch340[0].device
        return None

    def _probe_arduino_port(self):
        """返回当前确实存在的 Arduino 端口名，没有返回 None。
        配置了 --serial-port 时验证该端口仍在（板子拔出后端口会从列表消失）。"""
        cfg = self.config.serial_port
        if cfg:
            try:
                from serial.tools import list_ports
                present = {p.device for p in list_ports.comports()}
            except Exception:
                return cfg  # 列端口本身失败时不武断判离线，信任手动配置
            return cfg if cfg in present else None
        return self._autodetect_arduino_port()

    def _set_hardware_online(self, online: bool, reason: str = "") -> bool:
        """更新硬件在线状态，仅在状态翻转时打印一次，返回是否发生翻转。"""
        with self._hw_lock:
            changed = self._hardware_online != online
            self._hardware_online = online
        if changed:
            if online:
                log("已连接 Arduino 硬件键盘（硬件注入恢复）")
            else:
                soft = self._software_fallback_name()
                log(f"⚠ 未检测到 Arduino 硬件（{reason or '未插 / 已拔出'}），"
                    f"自动退回软件模拟按键（{soft}，扫描码）；后台每 30 秒检测，插回后自动切回硬件")
        return changed

    def _attach_arduino(self) -> bool:
        """幂等地尝试连上 Arduino：已连接或本次连上返回 True，探测不到/打开失败 False。
        按键路径与后台监测线程共用这一处，保证重连逻辑只有一份。"""
        with self._serial_lock:
            if self._arduino is not None:
                self._set_hardware_online(True)
                return True
            port = self._probe_arduino_port()
            if not port:
                self._set_hardware_online(False, reason="探测不到串口")
                return False
            s = None
            try:
                import serial
                s = serial.Serial(port, 115200, timeout=1)
                # 打开串口的 DTR 会让 Leonardo/Pro Micro 复位，ready 横幅晚 1~2 秒才打印；
                # 跳过空行读到 ready 关键词为止，最多 3 秒。读不到也不判失败——
                # 能在 VID 过滤后打开串口即认为是我们的板子（ready 仅用于日志）。
                ready = ""
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    line = s.readline()
                    if line and line.strip():
                        text = line.decode(errors="ignore").strip()
                        if text:
                            ready = text
                            if "ready" in text.lower():
                                break
                    time.sleep(0.1)
            except Exception as e:
                self._set_hardware_online(False, reason=f"串口打开失败 {port}: {e}")
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
                return False
            self._arduino = s
            # v2 固件支持 RELEASE（松开所有键）：每次连上先清一遍悬键，
            # 让"出错断开→重连"成为真正的自愈。v1 旧固件回 ERR，无害。
            try:
                s.write(b"RELEASE\n")
                s.readline()
            except Exception:
                pass
            # 解析固件版本：READY 横幅只在复位后打印一次、DTR 复位时序不保证能读到，
            # 故横幅之外再用 v3 独有命令做一次"行为探测"交叉验证，两者不一致以探测为准。
            import re
            fw_version = None
            if ready:
                mver = re.search(r"ready\s*v?(\d+)", ready, re.IGNORECASE)
                if mver:
                    fw_version = int(mver.group(1))
            # 行为探测：CHORD 7 1（n=7 超上限，且 n 后有空格使 s0 有效）。
            #   v3 回 ERR badn；v2 无 CHORD 分支，回 ERR unknown。该命令不触发任何按键。
            try:
                s.reset_input_buffer()
                s.write(b"CHORD 7 1\n")
                probe = ""
                t0 = time.time()
                while time.time() - t0 < 1.0:
                    ln = s.readline()
                    if ln and ln.strip():
                        probe = ln.decode(errors="ignore").strip().lower()
                        break
                if "badn" in probe:
                    probe_ver = 3
                elif "unknown" in probe:
                    probe_ver = 2
                else:
                    probe_ver = None
                if probe_ver is not None:
                    if fw_version is not None and fw_version != probe_ver:
                        log(f"固件横幅报告 v{fw_version} 与行为探测 v{probe_ver} 不一致，以行为探测为准")
                    fw_version = probe_ver
            except Exception:
                pass
            if fw_version is None:
                fw_version = 2
            self._firmware_version = fw_version
            log(f"Arduino connected on {port} (firmware v{fw_version}): "
                f"{ready or '(固件未上报 ready 横幅)'}")
            self._set_hardware_online(True)
            return True

    def _ensure_arduino(self):
        """按键路径用：拿到已连串口；连不上抛 HardwareLostError（调用方据此软补发）。"""
        if self._arduino is not None:
            return self._arduino
        if self._attach_arduino():
            return self._arduino
        raise HardwareLostError("Arduino 未连接，已退回软件模拟按键")

    def _mark_hardware_lost(self, reason: str) -> None:
        """通信中途失败：关串口、置离线（带状态翻转日志）。"""
        self._drop_arduino()
        self._set_hardware_online(False, reason=reason)

    # ---- 后台硬件热插拔监测（每 30 秒）----
    HW_POLL_INTERVAL = 30.0

    # ---- 软件按键可靠性（无硬件/兜底时减少吞键）----
    # 最小按压时长（keyDown→keyUp 之间，过短游戏会漏识别）；SendInput 失败重试次数。
    SOFT_MIN_PRESS_MS = float(os.environ.get("SKY_SOFT_MIN_PRESS_MS", "50"))
    SOFT_SEND_RETRIES = int(os.environ.get("SKY_SOFT_SEND_RETRIES", "3"))

    def start_hardware_monitor(self) -> None:
        """启动时调一次：立即探测当前硬件并打印后端，然后起 30 秒守护线程。
        幂等：重复调用不会起多个线程。"""
        if self._resolve_input_backend_name() != "arduino":
            # 用户显式选了软件/手柄后端，就不需要硬件守护
            return
        self._attach_arduino()
        if not self._hardware_online:
            self._warn_software_mode(force=True)
        if self._hw_monitor_thread is None:
            self._hw_monitor_stop.clear()
            t = threading.Thread(target=self._hw_monitor_loop, name="sky-hw-monitor", daemon=True)
            self._hw_monitor_thread = t
            t.start()

    def _warn_software_mode(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_software_warn >= self._hw_warn_interval:
            self._last_software_warn = now
            if not force:
                soft = self._software_fallback_name()
                log(f"当前无 Arduino 硬件，使用软件模拟按键（{soft}）；插回板子后会自动切回硬件")

    def _hw_monitor_loop(self) -> None:
        while not self._hw_monitor_stop.wait(self.HW_POLL_INTERVAL):
            try:
                port = self._probe_arduino_port()
                if port is None:
                    # 设备不在：若之前连着，说明被拔出
                    if self._arduino is not None:
                        self._mark_hardware_lost("USB 被拔出")
                    else:
                        with self._hw_lock:
                            self._hardware_online = False
                        self._warn_software_mode()
                else:
                    # 设备在：没连就尝试连回（自动重连），已连则保持
                    if self._arduino is None:
                        self._attach_arduino()
                    else:
                        self._set_hardware_online(True)
            except Exception as e:
                # 监测线程永不因异常退出
                log(f"硬件监测异常（忽略）: {e}")

    # 游戏窗口标题（精确前缀，不用泛匹配——"Sky"会误中 cmd 标题里的 sky-mcp-server）
    GAME_TITLE_PREFIXES = ("光·遇", "Sky: Children of the Light")

    def _game_is_foreground(self) -> bool:
        """开发板是真键盘，按键会进当前前台窗口。游戏不在前台时必须拒绝按键，
        否则会把 C / Ctrl+V 按进 Aki 的桌面（2026-07-03 两次'整机卡死'的元凶）。

        2026-10-20 修（P0）：`strict=True` 时**忽略** SKY_SKIP_FOCUS 旁路，做真实前台
        校验。SKY_SKIP_FOCUS=1 原本的含义是"不抢焦点"（不 activate 窗口），但它同时把
        `press_key` / `ensure_game_foreground` 的校验也一并旁路了 —— 于是 Arduino 真键盘
        会把 Ctrl+V 粘进用户正在编辑的文件。现在拆开：跳过"抢焦点"，但不跳过"校验"。
        """
        import os
        if os.environ.get("SKY_SKIP_FOCUS", "0") == "1" and not self._focus_strict():
            return True
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            fg_hwnd = u32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(fg_hwnd, buf, 256)
            title = buf.value
            # 1) 标题前缀匹配（游戏主窗口 TgcMainWindow，标题"光·遇"）
            if any(title.startswith(p) for p in self.GAME_TITLE_PREFIXES):
                return True
            # 2) 进程匹配：按 enter 打开聊天输入框时，游戏会激活一个无标题的
            #    TgcDialogClass 对话框（屏幕外，承载 IME），它属于游戏进程、按键安全；
            #    只按标题会误判为"不在前台"。
            fg_pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(fg_pid))
            game_pid = self._game_pid()
            if game_pid and fg_pid.value == game_pid:
                return True
            return False
        except Exception:
            return True  # 检查本身失败时不拦截，避免误伤

    def _fg_title(self) -> str:
        """返回当前前台窗口标题（仅用于错误信息 / 诊断，便于定位误判）。"""
        try:
            info = self._foreground_window()
            return info.get("title", "") if info else ""
        except Exception:
            return ""

    def _game_pid(self) -> int | None:
        """游戏主窗口（TgcMainWindow）的进程ID。"""
        try:
            import ctypes
            from ctypes import wintypes
            win_info = self.find_window()
            if not win_info:
                return None
            import pygetwindow as gw
            matches = gw.getWindowsWithTitle(win_info["title"])
            if not matches:
                return None
            hwnd = self._window_handle(matches[0])
            if not hwnd:
                return None
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value) if pid.value else None
        except Exception:
            return None

    def _drop_arduino(self) -> None:
        """断开串口。下次指令重开串口时 DTR 会触发板子复位 → 固件重启自动松开所有键。"""
        try:
            if self._arduino is not None:
                self._arduino.close()
        except Exception:
            pass
        self._arduino = None

    def _arduino_cmd(self, cmd: str) -> None:
        # ThreadingHTTPServer 是并发的：不加锁时两个请求的字节会在串口交错，
        # 板子解析出乱码指令，最坏情况把 Ctrl 等键永久卡死（键盘鼠标全瘫）。
        # 任何异常/坏应答一律断开串口，靠重连复位自愈。
        if not self._game_is_foreground():
            raise SkyError("游戏不在前台，拒绝按键（安全保护：防止按进桌面/其他窗口）"
                          f"。当前前台窗口=「{self._fg_title()}」")
        with self._serial_lock:
            try:
                s = self._ensure_arduino()
                s.write(cmd.encode())
                resp = s.readline().decode().strip()
            except HardwareLostError:
                raise  # 连接失败已在 _attach_arduino 内置离线状态
            except SkyError:
                raise
            except Exception:
                self._mark_hardware_lost("通信中断/串口异常")
                raise HardwareLostError("Arduino 通信中断，本次改用软件模拟")
            if resp != "OK":
                self._mark_hardware_lost(f"固件返回 {resp!r}")
                raise HardwareLostError(f"Arduino 返回 {resp!r}，本次改用软件模拟")

    def _arduino_press(self, keycode: int, duration_ms: int = 80) -> None:
        self._arduino_cmd(f"PRESS {keycode} {duration_ms}\n")

    def _arduino_hotkey(self, key1: int, key2: int, duration_ms: int = 50) -> None:
        self._arduino_cmd(f"HOTKEY {key1} {key2} {duration_ms}\n")

    def _arduino_chord(self, keycodes: list[int], duration_ms: int = 120) -> None:
        """v3 固件 CHORD 命令：多个键同时按下、保持后一起松开（真和弦）。
        仅在 _firmware_version>=3 时调用；USB HID 6KRO 上限 6 键。"""
        keys = [int(k) for k in keycodes][:6]
        if not keys:
            return
        ms = max(1, min(int(duration_ms), 2000))
        cmd = "CHORD " + str(len(keys)) + " " + " ".join(str(k) for k in keys) + f" {ms}\n"
        self._arduino_cmd(cmd)

    ARDUINO_KEY_MAP = {
    "enter": 176,
    "escape": 177,
    "tab": 179,
    "backspace": 42,
    "space": 32,
    "w": 119, "a": 97, "s": 115, "d": 100,
    "f": 102, "q": 113, "e": 101, "c": 99,
    "shift": 129,
    "1": 49, "2": 50, "3": 51, "4": 52,
    "5": 53, "6": 54, "7": 55, "8": 56,
    "9": 57, "0": 48,
    # 方向键（Arduino Keyboard 库键码）。固件 PRESS 透传任意键码，无需重烧
    "right": 215, "left": 216, "down": 217, "up": 218,
    # 光遇 15 键琴键（ASCII 码，Arduino Keyboard.press 接受可打印字符）。
    # 上排 y u i o p / 中排 h j k l ; / 下排 n m , . /
    "y": 121, "u": 117, "i": 105, "o": 111, "p": 112,
    "h": 104, "j": 106, "k": 107, "l": 108, ";": 59,
    "n": 110, "m": 109, ",": 44, ".": 46, "/": 47,
}

    def _clipboard(self):
        if self._pyperclip is None:
            self._pyperclip = load_optional("pyperclip")
        if self._pyperclip is None:
            raise SkyError("Missing dependency: pyperclip. Run: pip install -r requirements.txt")
        return self._pyperclip

    def _hotkey(self, *keys: str, duration_ms: int = 30, backend: str | None = None) -> None:
        self._press_combo([normalize_key(key) for key in keys], duration_ms, backend=backend)

    def _mouse_module(self):
        if self._pyautogui is None:
            self._pyautogui = load_optional("pyautogui")
        if self._pyautogui is None:
            raise SkyError("Missing dependency: pyautogui. Run: pip install -r requirements.txt")
        self._pyautogui.FAILSAFE = False
        self._pyautogui.PAUSE = 0.01
        return self._pyautogui

    def _soft_event(self, inp, fn, key) -> bool:
        """软件 keyDown/keyUp 的可靠包装：SendInput 没把事件插进去时重试。

        pydirectinput 的 keyDown/keyUp 返回 bool（成功插入事件=True）；pyautogui 返回 None。
        只把明确的 False 当失败重试，True/None 都视为成功（兼容 pyautogui）。"""
        for attempt in range(max(1, self.SOFT_SEND_RETRIES)):
            try:
                ok = fn(key)
            except Exception:
                ok = False
            if ok is not False:
                return True
            time.sleep(0.03 * (attempt + 1))
        log(f"软件按键 {key} 经 {self.SOFT_SEND_RETRIES} 次仍未成功注入（可能被系统/游戏吞掉）")
        return False

    def _press_combo(self, keys: list[str], duration_ms: int = 30, backend: str | None = None) -> None:
        if not keys:
            return
        inp = self._input_module(backend)
        # 软件按键保证最小按压时长，避免 down/up 太快被游戏吞掉（Arduino 走 _arduino_press，不经过这里）
        duration = max(int(duration_ms), int(self.SOFT_MIN_PRESS_MS)) / 1000
        modifiers = keys[:-1]
        main = keys[-1]
        for mod in modifiers:
            self._soft_event(inp, inp.keyDown, mod)
        try:
            self._soft_event(inp, inp.keyDown, main)
            time.sleep(duration)
            self._soft_event(inp, inp.keyUp, main)
        finally:
            for mod in reversed(modifiers):
                try:
                    self._soft_event(inp, inp.keyUp, mod)
                except Exception:
                    pass

    def _ensure_us_keyboard_layout(self) -> None:
        """软件发键前加载英文（美式）键盘布局，降低中文输入法/非英文布局吞键。

        小技巧参考自 windhide amd_robot.py 的 set_us_keyboard_layout。
        LoadKeyboardLayout 只把英文布局加载/激活到当前线程，不改动用户系统默认
        布局；pydirectinput 本身走硬件扫描码，这一步是额外保险。幂等、失败静默。
        """
        if self._us_layout_set or not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            ctypes.windll.user32.LoadKeyboardLayoutW("00000409", 1)  # 0x409=en-US, KLF_ACTIVATE=1
            self._us_layout_set = True
        except Exception:
            pass

    def _chord_soft(self, keys: list[str], duration_ms: int = 120, backend: str | None = None) -> None:
        """软件后端真和弦：所有键一起 down、保持、再一起 up（各键地位平等）。"""
        if not keys:
            return
        inp = self._input_module(backend)
        self._ensure_us_keyboard_layout()
        duration = max(int(duration_ms), int(self.SOFT_MIN_PRESS_MS)) / 1000
        downed: list[str] = []
        try:
            for k in keys:
                if self._soft_event(inp, inp.keyDown, k):
                    downed.append(k)
            time.sleep(duration)
        finally:
            for k in reversed(downed):
                try:
                    self._soft_event(inp, inp.keyUp, k)
                except Exception:
                    pass

    def press_chord(self, keys: list[str], duration_ms: int = 120, backend: str | None = None) -> str:
        """弹奏一个单音/和弦的统一出口（不抢焦点，供播放线程高频调用）。

        - Arduino v3 固件：CHORD 真和弦（1~6 键）；
        - Arduino v2 固件：单键 PRESS、双键 HOTKEY；三键及以上整组改软件兜底
          （保证不丢音，仅提示一次）；
        - 纯软件后端：pydirectinput/pyautogui 多键同时按下（真和弦）。
        """
        norm = [normalize_key(k) for k in keys if k]
        if not norm:
            return "empty chord"
        # 播放途中不抢焦点，但严格模式下前台不是游戏就拒绝（避免打进别的窗口）。
        if self._focus_strict() and not self._game_is_foreground():
            raise SkyError("Sky window is not foreground: refusing to play note "
                           "(set SKY_FOCUS_STRICT=0 to restore the unsafe behaviour)"
                           f". 当前前台窗口=「{self._fg_title()}」")
        resolved = self._effective_backend(backend)
        if resolved == "arduino":
            kcs = [self.ARDUINO_KEY_MAP.get(k) for k in norm]
            if any(k is None for k in kcs):
                soft = self._software_fallback_name()
                self._chord_soft(norm, duration_ms, backend=soft)
                return f"chord {norm} via {soft}(unmapped hardware key)"
            try:
                if self._firmware_version >= 3 or len(norm) <= 2:
                    if len(norm) == 1:
                        self._arduino_press(kcs[0], duration_ms)
                    elif len(norm) == 2:
                        self._arduino_hotkey(kcs[0], kcs[1], duration_ms)
                    else:
                        self._arduino_chord(kcs, duration_ms)
                    return f"arduino chord {norm} for {int(duration_ms)}ms"
                # v2 固件不支持三键和弦：整组软件兜底，保证不丢音。
                soft = self._software_fallback_name()
                if not self._chord_soft_fallback_notified:
                    log("当前 Arduino 固件为 v2，不支持三键和弦；三音及以上已自动用软件"
                        "模拟完整弹奏。烧录 sky_keyboard_v3 固件后可由硬件完整弹奏。")
                    self._chord_soft_fallback_notified = True
                self._chord_soft(norm, duration_ms, backend=soft)
                return f"chord {norm} via {soft}(firmware v2, 3+ keys)"
            except HardwareLostError:
                soft = self._software_fallback_name()
                log(f"硬件不可用，和弦 {norm} 改用软件模拟（{soft}）补发")
                self._chord_soft(norm, duration_ms, backend=soft)
                return f"chord {norm} via {soft}(hardware unavailable)"
        # 纯软件后端（手柄无琴键映射，也落到软件兜底）
        self._chord_soft(norm, duration_ms, backend=resolved)
        return f"chord {norm} for {int(duration_ms)}ms via {resolved}"

    def _tap_key(self, key: str, duration_ms: int = 35, backend: str | None = None) -> None:
        key = normalize_key(key)
        if "+" in key or "-" in key:
            parts = [normalize_key(p) for p in key.replace("+", "-").split("-") if p]
            self._press_combo(parts, duration_ms, backend=backend)
            return
        self._press_combo([key], duration_ms, backend=backend)

    def _software_modifier_v(self, soft: str) -> None:
        if sys.platform == "darwin":
            self._hotkey("command", "v", backend=soft)
        else:
            self._hotkey("ctrl", "v", backend=soft)

    def _tap_with_fallback(self, key_name: str, keycode: int | None,
                           duration_ms: int, backend: str | None = None) -> str:
        """单个键的统一出口：硬件在线走 Arduino，硬件掉线/无映射则用软件补发。
        key_name 为规范化键名（软件模块用），keycode 为 Arduino USB 键码（硬件用）。"""
        resolved = self._effective_backend(backend)
        if resolved == "arduino":
            try:
                if keycode is None:
                    raise HardwareLostError(f"Arduino 键码表无 {key_name}")
                self._arduino_press(keycode, duration_ms)
                return f"arduino {key_name} for {int(duration_ms)}ms"
            except HardwareLostError:
                soft = self._software_fallback_name()
                log(f"硬件不可用，{key_name} 改用软件模拟（{soft}）补发")
                self._tap_key(key_name, duration_ms, backend=soft)
                return f"{key_name} for {int(duration_ms)}ms via {soft}(hardware unavailable)"
        self._tap_key(key_name, duration_ms, backend=resolved)
        return f"pressed {key_name} for {int(duration_ms)}ms via {resolved}"

    def _paste_text(self, message: str, backend: str | None = None) -> None:
        clip = self._clipboard()
        clip.copy(message)
        time.sleep(0.05)
        resolved = self._effective_backend(backend)
        if resolved == "arduino":
            try:
                self._arduino_hotkey(128, 118, 50)
            except HardwareLostError:
                soft = self._software_fallback_name()
                log(f"硬件不可用，Ctrl+V 改用软件模拟（{soft}）补发")
                self._software_modifier_v(soft)
        elif sys.platform == "darwin":
            self._hotkey("command", "v", backend=resolved)
        else:
            self._hotkey("ctrl", "v", backend=resolved)
        time.sleep(0.08)

    def _foreground_window(self) -> dict[str, Any] | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, len(buf))
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return {"hwnd": int(hwnd), "title": buf.value, "pid": int(pid.value)}
        except Exception:
            return None

    def _force_foreground_window(self, hwnd: int) -> None:
        if not sys.platform.startswith("win"):
            return

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        sw_restore = 9

        # 用 AttachThreadInput 把当前线程的输入队列与前台/目标线程绑定，
        # 绑定后 SetForegroundWindow 不受"仅最近输入进程可抢前台"限制。
        # 不模拟 ALT（会触发"任务切换"）、不切 topmost（会闪窗）。
        foreground_hwnd = user32.GetForegroundWindow()
        foreground_pid = wintypes.DWORD()
        target_pid = wintypes.DWORD()
        foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(foreground_pid))
        target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
        current_thread = kernel32.GetCurrentThreadId()

        attached = set()
        for thread_id in {foreground_thread, target_thread, current_thread}:
            if thread_id and user32.AttachThreadInput(current_thread, thread_id, True):
                attached.add(thread_id)
        try:
            user32.ShowWindow(hwnd, sw_restore)
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            for thread_id in attached:
                user32.AttachThreadInput(current_thread, thread_id, False)

    def _window_handle(self, win) -> int | None:
        hwnd = getattr(win, "_hWnd", None) or getattr(win, "hWnd", None)
        return int(hwnd) if hwnd else None

    def _click_window_center(self, win_info: dict[str, Any]) -> None:
        mouse = self._mouse_module()
        x = int(win_info["left"] + win_info["width"] / 2)
        y = int(win_info["top"] + win_info["height"] / 2)
        mouse.click(x, y)

    def find_window(self) -> dict[str, Any] | None:
        titles = [self.config.window_title] if self.config.window_title else [
            "光·遇",
            "光遇",
            "Sky",
            "Sky: Children of the Light",
            "Sky Children of the Light",
            "光遇",
        ]
        try:
            import pygetwindow as gw
        except Exception:
            return None

        for title in [t for t in titles if t]:
            for win in gw.getWindowsWithTitle(title):
                if win.isMinimized:
                    continue
                return {
                    "title": win.title,
                    "left": int(win.left),
                    "top": int(win.top),
                    "width": int(win.width),
                    "height": int(win.height),
                }
        return None

    def _focus_strict(self) -> bool:
        """是否启用"严格前台校验"（默认开）。即使 SKY_SKIP_FOCUS=1（不抢焦点），
        按键前仍会校验游戏是否真在前台，不在就拒绝按 —— 设 SKY_FOCUS_STRICT=0 回退旧行为。"""
        import os
        return os.environ.get("SKY_FOCUS_STRICT", "1") != "0"

    def focus_game(self, force: bool = False) -> dict[str, Any]:
        # Arduino 硬件模式下可跳过"抢焦点"（用户自己确保游戏在前台）；
        # 但"校验是否在前台"不再一并跳过（SKY_FOCUS_STRICT，见 _game_is_foreground）。
        # force=True 时（如 send_chat 发送前）忽略 SKY_SKIP_FOCUS，必须把游戏拉到前台，
        # 否则发送那一刻焦点不在游戏，只会被拒绝、不会自动拉前台。
        import os
        if os.environ.get("SKY_SKIP_FOCUS", "0") == "1" and not force:
            win_info = self.find_window()
            if win_info:
                return {**win_info, "focused": True, "foreground": None}
            raise SkyError("Sky window not found. Start Sky first.")
        try:
            import pygetwindow as gw
        except Exception as exc:
            raise SkyError(f"pygetwindow is required to focus the Sky window: {exc}") from exc

        win_info = self.find_window()
        if not win_info:
            raise SkyError("Sky window not found. Start Sky first, or pass --window-title.")
        matches = gw.getWindowsWithTitle(win_info["title"])
        if not matches:
            raise SkyError("Sky window disappeared before focus.")
        win = matches[0]
        hwnd = self._window_handle(win)
        # 快速路径：游戏已经在前台就直接返回，不做 activate / 强制前台 / 点击窗口中心等
        # 任何抢焦点动作，避免多余操作（尤其点击画面中心）反而干扰当前焦点。
        if sys.platform.startswith("win") and hwnd:
            _now_fg = self._foreground_window()
            if _now_fg and _now_fg.get("hwnd") == hwnd:
                return {**win_info, "focused": True, "foreground": _now_fg}
        if win.isMinimized:
            win.restore()
        # 直接用 AttachThreadInput 拉前台（本地实测稳定可靠），不先 win.activate()
        # （pygetwindow 的 activate 会先做一次受限的 SetForegroundWindow，反而引入竞态）。
        if sys.platform.startswith("win") and hwnd:
            foreground = None
            for attempt in range(2):
                self._force_foreground_window(hwnd)
                time.sleep(0.12)
                foreground = self._foreground_window()
                if foreground and foreground.get("hwnd") == hwnd:
                    break
            # 最后手段：软件点击窗口中心
            if not foreground or foreground.get("hwnd") != hwnd:
                self._click_window_center(win_info)
                time.sleep(0.15)
                foreground = self._foreground_window()
            if foreground and foreground.get("hwnd") != hwnd:
                raise SkyError(
                    f"Sky window found but not focused. Foreground window is: {foreground.get('title')}")
            return {**win_info, "focused": True, "foreground": foreground}
        # 非 Windows 回退：pygetwindow activate
        try:
            win.activate()
        except Exception as exc:
            raise SkyError(f"Failed to activate Sky window: {exc}") from exc
        return {**win_info, "focused": True, "foreground": self._foreground_window()}

    def ensure_game_foreground(self) -> dict[str, Any]:
        # Arduino 硬件模式下可跳过"抢焦点"，但**不跳过"校验"**（2026-11-01 修）：
        # 曾经这里直接返回 focused=True/foreground=None，等于对外宣称"已在前台"，
        # type_text 拿到这个结果就去 Ctrl+V，内容会粘进用户当时正在编辑的窗口。
        import os
        if os.environ.get("SKY_SKIP_FOCUS", "0") == "1":
            win_info = self.find_window()
            if not win_info:
                raise SkyError("Sky window not found. Start Sky first.")
            if self._focus_strict() and not self._game_is_foreground():
                raise SkyError("Sky window is not foreground: refusing to continue "
                               "(SKY_SKIP_FOCUS=1 only means 'do not steal focus', it does not "
                               "skip verification; set SKY_FOCUS_STRICT=0 for the old unsafe behaviour)"
                               f". 当前前台窗口=「{self._fg_title()}」")
            return {**win_info, "focused": True, "foreground": self._foreground_window()}
        win_info = self.find_window()
        if not win_info:
            raise SkyError("Sky window not found. Start Sky first, or pass --window-title.")
        foreground = self._foreground_window()
        if not sys.platform.startswith("win"):
            return {**win_info, "focused": True, "foreground": foreground}
        try:
            import pygetwindow as gw
        except Exception as exc:
            raise SkyError(f"pygetwindow is required to verify the Sky window: {exc}") from exc
        matches = gw.getWindowsWithTitle(win_info["title"])
        hwnd = self._window_handle(matches[0]) if matches else None
        if hwnd and foreground and foreground.get("hwnd") != hwnd:
            raise SkyError(f"Sky window is not foreground. Foreground window is: {foreground.get('title')}")
        return {**win_info, "focused": True, "foreground": foreground}

    # ---------------- 光翼硬门的 MCP 侧闸（2026-11-01）----------------
    def set_wing_guard(self, active: bool) -> dict[str, Any]:
        """记录 sky-loop 推送的光翼防护状态。

        为什么要有这一层：光翼硬门原本只存在于 sky-loop 的 `_key()`，而 MCP 的
        `press_key` 是对外暴露的底层按键接口 —— 任何绕过 loop 的调用方（外部脚本、
        另一个会话、调试工具）都能直接按 F，等于硬门形同虚设。现在把状态机结果同步
        到输入边界，由 MCP 自己拒绝 F，不依赖调用方自律。
        """
        with self._wing_guard_lock:
            self._wing_guard_active = bool(active)
            self._wing_guard_at = time.time()
            active_now = self._wing_guard_active
            at = self._wing_guard_at
        log(f"wing guard <- {'ACTIVE (F blocked)' if active_now else 'clear'}")
        return {"guard_active": active_now, "updated_at": at}

    def wing_guard_state(self) -> dict[str, Any]:
        with self._wing_guard_lock:
            active = self._wing_guard_active
            at = self._wing_guard_at
        return {
            "guard_active": active,
            "age_sec": round(time.time() - at, 1) if at else None,
        }

    def _f_press_blocked(self, key: str, wing_pickup: bool = False) -> bool:
        """MCP 层 F 闸：防护态下拒绝 F，只放行显式标注的自动拾取。

        wing_pickup=True 只应由 sky-loop 的 wing_pickup_step 传入 —— 它背后还有
        「账号有翼 + 明确开启」两把钥匙，属于有意拾取，不是误触。
        """
        if wing_pickup:
            return False
        if str(key).strip().lower() != "f":
            return False
        with self._wing_guard_lock:
            return self._wing_guard_active

    def press_key(self, key: str, duration_ms: int = 80, backend: str | None = None,
                  wing_pickup: bool = False) -> str:
        key = normalize_key(key)
        if self._f_press_blocked(key, wing_pickup):
            raise SkyError("wing guard active: refusing F "
                           "(sky-loop reported wing_light nearby; only the double-key "
                           "auto-pickup path may pass wing_pickup=true)")
        self.focus_game()
        # 严格前台校验：SKY_SKIP_FOCUS=1 只表示"不抢焦点"，不代表"可以打到别的窗口"。
        # Arduino 是真 USB 键盘，一旦前台不是光遇，这次按键会打进用户当前窗口。
        if self._focus_strict() and not self._game_is_foreground():
            raise SkyError("Sky window is not foreground: refusing to press key "
                           "(set SKY_FOCUS_STRICT=0 to restore the unsafe behaviour)"
                           f". 当前前台窗口=「{self._fg_title()}」")
        resolved = self._effective_backend(backend)
        if resolved == "gamepad":
            return self._gamepad_press(key, duration_ms)
        kc = self.ARDUINO_KEY_MAP.get(key)
        return self._tap_with_fallback(key, kc, duration_ms, backend)

    # ---------------- 弹琴（sky_song_player，CC BY-NC 署名 windhide）----------------
    def _project_root(self) -> Path:
        return Path(__file__).resolve().parent

    def _song_dirs(self) -> list[str]:
        """曲谱搜索目录（按优先级，去重，只保留真实存在的）：
        1) songs/                  项目曲谱目录（本地可放整库；Git 只随仓库分发少量自制/公有领域示例）
        2) user_data/songs/        用户自备曲谱（gitignore，不进 Git）
        3) SKY_SONGS_DIR 环境变量   分号分隔的额外目录
        4) 本机已安装的星星弹琴软件曲库（若存在，零配置直读，不随仓库分发）

        跨目录由 list_song_files 按歌名去重，项目 songs/ 优先，重复曲谱不会重复计数。
        """
        root = self._project_root()
        candidates = [
            root / "songs",
            root / "user_data" / "songs",
        ]
        for part in os.environ.get("SKY_SONGS_DIR", "").split(os.pathsep):
            part = part.strip()
            if part:
                candidates.append(Path(part))
        home = Path.home()
        for rel in (
            r"Desktop\systemMusic",
            r"Desktop\sky-music\Sky_Music\resources\systemMusic",
            r"Desktop\sky-music\Sky_Music\resources\myImport",
        ):
            candidates.append(home / rel)
        dirs: list[str] = []
        seen: set[str] = set()
        for c in candidates:
            sp = str(c)
            if sp not in seen and c.is_dir():
                seen.add(sp)
                dirs.append(sp)
        return dirs

    def list_songs(self, limit: int = 300) -> dict[str, Any]:
        if sky_song_player is None:
            raise SkyError(f"弹琴模块加载失败：{_SONG_IMPORT_ERROR}")
        dirs = self._song_dirs()
        songs = sky_song_player.list_song_files(dirs)
        view = [{"name": s["name"], "size": s["size"], "dir": s["dir"]}
                for s in songs[:max(0, limit)]]
        return {"total": len(songs), "showing": len(view), "dirs": dirs, "songs": view}

    def _get_song_player(self):
        if sky_song_player is None:
            raise SkyError(f"弹琴模块加载失败：{_SONG_IMPORT_ERROR}")
        if self._song_player is None:
            self._song_player = sky_song_player.SongPlayer(
                chord_fn=lambda keys, hold_ms: self.press_chord(
                    keys, hold_ms, backend=getattr(self, "_song_backend", None)),
            )
        return self._song_player

    def play_song(self, name: str, speed: float = 1.0, auto_focus: bool = True,
                  backend: str | None = None, choice: str | None = None,
                  humanize: int = 0, instrument: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
        """弹一首曲谱。

        choice：同名多版本时的选择（"1"/"第2个"/"烛子" 等，见 sky_song_player.resolve_choice）。
        命中多个且没给 choice 时返回 multiple_matches（带候选清单），由调用方去问玩家。
        humanize：0 关闭（默认）/ 1 轻微 / 2 明显（音符微抖动 + 句间呼吸）。
        instrument：piano/violin/flute/harmonica；不传则用本地设置（默认 piano）。
        """
        if not isinstance(name, str) or not name.strip():
            raise SkyError("play_song 需要指定歌名 name")
        player = self._get_song_player()
        self._song_backend = backend
        dirs = self._song_dirs()
        # 先看这首歌到底有几个**不同**版本（重复上传的同一份已合并）：
        # ≥2 个就一定要问玩家要哪个 —— 精确名命中也不例外。曲库里同名谱常常同时
        # 存在裸版/署名版/变奏版，替玩家随便挑一个正是 2026-10-28 要修的"随机拿版本"。
        cands = sky_song_player.find_song_versions(name, dirs)
        view = [{"index": c["index"], "label": c["label"], "name": c["name"],
                 "total_ms": c["total_ms"], "duration": c.get("duration", ""),
                 "is_community": c["is_community"],
                 "transcribed_by": c.get("transcribed_by", "")} for c in cands]
        if not cands:
            # 兜底：分组万一漏掉，退回单曲查找，别让玩家"明明有谱却说找不到"
            _fb = sky_song_player.find_song(name, dirs)
            if _fb is None:
                # 近似候选：让调用方（AI）知道"最像的是哪几首"，好反问玩家而不是
                # 干等 / 硬说自己弹了。2026-11-12 真机：not_found 连发 7 次、
                # AI 拿不到原因，一直说"在弹啦"。
                _sugg: list[dict] = []
                try:
                    _sugg = sky_song_player.find_song_similar(name, dirs, limit=5)
                except Exception as _e:  # 老版本模块没有该函数时不影响 not_found 主体
                    log(f"[Song] 近似候选查询失败（忽略）：{_e}")
                _sugg_names = [s["name"] for s in _sugg]
                _msg = "没有找到该曲谱（用 list_songs 查看可用曲目）"
                if _sugg_names:
                    _msg = ("没有叫「%s」的谱。曲库里最接近的是：%s"
                            % (name.strip(), "、".join("《%s》" % s for s in _sugg_names)))
                return {"ok": False, "reason": "not_found",
                        "message": _msg,
                        "suggestions": _sugg_names,
                        "searched_dirs": dirs}
            match = {"file": _fb["file"], "name": _fb["name"]}
        elif len(cands) > 1:
            if choice:
                picked = sky_song_player.resolve_choice(cands, choice)
                if picked is None:
                    return {"ok": False, "reason": "bad_choice",
                            "message": f"没听懂要选哪个版本，回复 1~{len(cands)} 的序号或版本名",
                            "song_query": name.strip(), "matches": view}
                match = {"file": picked["file"], "name": picked["name"]}
                print(f"  [Song] 玩家选了第{picked['index']}个版本：{picked['name']}")
            else:
                return {"ok": False, "reason": "multiple_matches",
                        "message": f"「{name.strip()}」有 {len(cands)} 个不同版本，需要玩家选一个",
                        "song_query": name.strip(), "matches": view}
        else:
            match = {"file": cands[0]["file"], "name": cands[0]["name"]}
        try:
            sheet = sky_song_player.load_sheet(match["file"])
        except ValueError as e:
            return {"ok": False, "reason": "bad_sheet", "message": str(e)}
        if dry_run:
            # 只解析不演奏，也不抢焦点：调用方先知道"有几个版本/能不能弹"，
            # 避免为了问一句版本就把乐器界面按开（开了再按 C 发问句会把界面关掉）
            return {"ok": True, "dry_run": True, "song": sheet["name"],
                    "author": sheet.get("author", ""),
                    "transcribed_by": sheet.get("transcribed_by", ""),
                    "events": len(sheet["events"]), "total_ms": sheet["total_ms"]}
        if auto_focus:
            self.focus_game()
        if self._focus_strict() and not self._game_is_foreground():
            raise SkyError("Sky window is not foreground: refusing to play song "
                           "(set SKY_FOCUS_STRICT=0 to restore the unsafe behaviour)"
                           f". 当前前台窗口=「{self._fg_title()}」")
        # 乐器档位：钢琴按住不延音，管弦/吹奏可以拉长（决定每个音的保持时长）。
        # 优先用调用方传的 instrument，否则读本地设置（user_data/song_settings.json，玩家自己填）。
        _kind = str(instrument or "").strip().lower()
        if _kind not in sky_song_player.INSTRUMENT_PROFILES:
            try:
                from core.song_settings import kind_of as _kind_of
                _kind = _kind_of()
            except Exception:
                _kind = "piano"
        _prof = sky_song_player.instrument_profile(_kind)
        _hum = int(humanize or 0)
        player.play(sheet, speed=float(speed or 1.0), humanize=_hum,
                    sustain=bool(_prof["sustain"]), max_hold_ms=int(_prof["max_hold_ms"]))
        st = player.state()
        return {"ok": True, "message": f"开始弹奏：{sheet['name']}", "song": sheet["name"],
                "author": sheet.get("author", ""), "transcribed_by": sheet.get("transcribed_by", ""),
                "events": len(sheet["events"]), "total_ms": sheet["total_ms"],
                "backend": self._effective_backend(backend),
                "instrument": _prof["label"], "humanize": _hum,
                "status": st["status"], "progress_pct": st["progress_pct"], "speed": st["speed"]}

    def stop_song(self) -> dict[str, Any]:
        if self._song_player is None:
            return {"ok": True, "status": "idle", "message": "当前没有在弹奏"}
        self._song_player.stop()
        return {"ok": True, "status": "stopped", "message": "已停止弹奏"}

    def song_status(self) -> dict[str, Any]:
        if self._song_player is None:
            return {"status": "idle"}
        return self._song_player.state()

    def _stop_song_on_exit(self) -> None:
        try:
            if self._song_player is not None:
                self._song_player.stop()
        except Exception:
            pass

    def open_chat(self, key: str = "enter", duration_ms: int = 35, backend: str | None = None) -> str:
        self.focus_game()
        key = normalize_key(key)
        resolved = self._effective_backend(backend)
        if resolved == "gamepad":
            return self._gamepad_press(key, duration_ms)
        kc = self.ARDUINO_KEY_MAP.get(key, 176)
        return self._tap_with_fallback(key, kc, duration_ms, backend).replace("pressed", "tapped")

    def type_text(
        self,
        message: str,
        send: bool = True,
        enter_tap_ms: int = 35,
        backend: str | None = None,
        require_foreground: bool = True,
    ) -> str:
        if not isinstance(message, str) or not message.strip():
            raise SkyError("message must be a non-empty string")
        if len(message) > 240:
            raise SkyError("message is too long; keep it under 240 characters")

        if require_foreground:
            self.ensure_game_foreground()
        # 粘贴前再查一次前台（2026-11-01 修）：ensure 与 Ctrl+V 之间存在竞态，且历史版本
        # 在 SKY_SKIP_FOCUS=1 时会返回"伪成功"。send_chat 早就有这道检查，type_text 漏了。
        if self._focus_strict() and not self._game_is_foreground():
            raise SkyError("Sky window is not foreground: refusing to type "
                           "(Ctrl+V would paste into another window; SKY_FOCUS_STRICT=0 to override)"
                           f". 当前前台窗口=「{self._fg_title()}」")
        self._paste_text(message, backend=backend)
        if send:
            self._tap_with_fallback("enter", 176, enter_tap_ms, backend)
        action = "sent" if send else "typed"
        return f"{action} text: {message} via {self._effective_backend(backend)}"

    def send_chat(
        self,
        message: str,
        open_key: str = "enter",
        open_delay_ms: int = 180,
        assume_open: bool = False,
        send: bool = True,
        enter_tap_ms: int = 35,
        backend: str | None = None,
    ) -> str:
        if not isinstance(message, str) or not message.strip():
            raise SkyError("message must be a non-empty string")
        if len(message) > 240:
            raise SkyError("message is too long; keep it under 240 characters")

        if assume_open:
            self.ensure_game_foreground()
        else:
            self.focus_game(force=True)
            resolved = self._effective_backend(backend)
            open_norm = normalize_key(open_key)
            if resolved == "gamepad":
                self._gamepad_press(open_norm, enter_tap_ms)
            else:
                kc = self.ARDUINO_KEY_MAP.get(open_norm, 176)
                self._tap_with_fallback(open_norm, kc, enter_tap_ms, backend)
            time.sleep(max(0, open_delay_ms) / 1000)
        # 前台不是光遇时，下面的 Ctrl+V 会把聊天内容粘进用户正在编辑的窗口
        if self._focus_strict() and not self._game_is_foreground():
            raise SkyError("Sky window is not foreground: refusing to send chat "
                           "(Ctrl+V would paste into another window; SKY_FOCUS_STRICT=0 to override)"
                           f". 当前前台窗口=「{self._fg_title()}」")
        self._paste_text(message, backend=backend)
        if send:
            resolved_send = self._effective_backend(backend)
            if resolved_send == "gamepad":
                self._gamepad_press("enter", enter_tap_ms)
            else:
                self._tap_with_fallback("enter", 176, enter_tap_ms, backend)
        action = "sent" if send else "typed"
        return f"{action} chat: {message} via {self._effective_backend(backend)}"

    def _limit_image_size(self, image):
        max_width = max(1, int(self.config.screenshot_max_width))
        max_height = max(1, int(self.config.screenshot_max_height))
        scale = min(max_width / image.width, max_height / image.height, 1)
        if scale >= 1:
            return image
        resampling = getattr(self._image_cls, "Resampling", self._image_cls).LANCZOS
        return image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            resampling,
        )

    def screenshot_image(self):
        self.ensure_capture_deps()
        with self._mss.mss() as screen:
            win = self.find_window()
            if win:
                region = {
                    "left": win["left"],
                    "top": win["top"],
                    "width": max(1, win["width"]),
                    "height": max(1, win["height"]),
                }
            else:
                monitors = screen.monitors
                index = min(max(1, self.config.monitor), len(monitors) - 1)
                region = monitors[index]
            shot = screen.grab(region)
            image = self._image_cls.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if self.config.screenshot_scale != 1.0:
            scale = min(max(self.config.screenshot_scale, 0.2), 4.0)
            resampling = getattr(self._image_cls, "Resampling", self._image_cls).LANCZOS
            image = image.resize((int(image.width * scale), int(image.height * scale)), resampling)
        return self._limit_image_size(image)

    def screenshot_base64(self) -> str:
        image = self.screenshot_image()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def read_screen(self) -> dict[str, Any]:
        image = self.screenshot_image()
        with tempfile.NamedTemporaryFile(prefix="sky-mcp-", suffix=".png", delete=False) as tmp:
            path = tmp.name
        try:
            image.save(path)
            return {
                "ocr": self._detect_ocr_name(),
                "ocr_device": self._ocr_device,
                "image_size": {"width": image.width, "height": image.height},
                "texts": self._run_ocr(path),
            }
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _detect_ocr_name(self) -> str:
        if self._ocr_engine is not None:
            return self._ocr_name
        try:
            os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import PaddleOCR
            try:
                import paddle
                if paddle.device.is_compiled_with_cuda():
                    paddle.set_device("gpu:0")
                self._ocr_device = paddle.get_device()
            except Exception:
                self._ocr_device = "unknown"

            self._ocr_engine = PaddleOCR(use_angle_cls=False, lang="ch", det_limit_side_len=1920)

            self._ocr_name = f"paddleocr-mobile-{self._ocr_device}"
            return self._ocr_name
        except Exception:
            pass
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self._ocr_engine = pytesseract
            self._ocr_name = "tesseract"
            return self._ocr_name
        except Exception:
            pass
        self._ocr_name = "none"
        return self._ocr_name

    def _run_ocr(self, image_path: str) -> list[dict[str, Any]]:
        engine = self._detect_ocr_name()
        if engine.startswith("paddleocr"):
            return self._run_paddle_ocr(image_path)
        if engine == "tesseract":
            return self._run_tesseract_ocr(image_path)
        return [{"text": "No OCR engine installed. Install PaddleOCR or Tesseract.", "confidence": 0, "x": 0, "y": 0}]

    def _run_paddle_ocr(self, image_path: str) -> list[dict[str, Any]]:
        # 放大+增强对比度再OCR
        img = self._image_cls.open(image_path)
        img = img.resize((img.width * 2, img.height * 2), getattr(self._image_cls, "Resampling", self._image_cls).LANCZOS)
        try:
            from PIL import ImageEnhance
            img = ImageEnhance.Contrast(img).enhance(1.5)
            img = ImageEnhance.Sharpness(img).enhance(1.5)
        except Exception:
            pass
        enhanced_path = image_path + ".enhanced.png"
        img.save(enhanced_path)
        try:
            try:
                result = self._ocr_engine.ocr(enhanced_path, cls=True)
            except Exception:
                result = self._ocr_engine.ocr(enhanced_path)
        finally:
            try:
                os.unlink(enhanced_path)
            except OSError:
                pass
        if not result:
            return []

        texts: list[dict[str, Any]] = []
        if isinstance(result[0], dict):
            for page in result:
                rec_texts = page.get("rec_texts") or []
                rec_scores = page.get("rec_scores") or []
                rec_boxes = page.get("rec_polys") or page.get("dt_polys") or []
                for index, text in enumerate(rec_texts):
                    points = rec_boxes[index] if index < len(rec_boxes) else []
                    if hasattr(points, "tolist"):
                        points = points.tolist()
                    xs = [p[0] for p in points] if points else [0]
                    ys = [p[1] for p in points] if points else [0]
                    texts.append({
                        "text": str(text),
                        "confidence": float(rec_scores[index]) if index < len(rec_scores) else 0,
                        "x": int(min(xs) / 2),
                        "y": int(min(ys) / 2),
                    })
            return texts

        for line in result[0] or []:
            box, (text, confidence) = line[0], line[1]
            texts.append({
                "text": str(text),
                "confidence": float(confidence),
                "x": int(min(p[0] for p in box) / 2),
                "y": int(min(p[1] for p in box) / 2),
            })
        return texts

    def _run_tesseract_ocr(self, image_path: str) -> list[dict[str, Any]]:
        img = self._image_cls.open(image_path)
        # 放大2倍
        img = img.resize((img.width * 2, img.height * 2), getattr(self._image_cls, "Resampling", self._image_cls).LANCZOS)
        # 增强对比度
        try:
            from PIL import ImageEnhance
            img = ImageEnhance.Contrast(img).enhance(2.0)
            img = ImageEnhance.Sharpness(img).enhance(2.0)
        except Exception:
            pass
        # 保存处理后的图片给tesseract
        enhanced_path = image_path + ".enhanced.png"
        img.save(enhanced_path)
        try:
            text = self._ocr_engine.image_to_string(self._image_cls.open(enhanced_path), lang="chi_sim+eng")
        finally:
            try:
                os.unlink(enhanced_path)
            except OSError:
                pass
        return [
            {"text": line.strip(), "confidence": 0.5, "x": 0, "y": 0}
            for line in text.splitlines()
            if line.strip()
        ]


def normalize_key(key: str) -> str:
    key = str(key).lower().strip()
    aliases = {
        "return": "enter",
        "esc": "escape",
        "spacebar": "space",
        "cmd": "command",
        "win": "windows",
        "ctrl": "ctrl",
        "control": "ctrl",
        "option": "alt",
    }
    return aliases.get(key, key)


def text_content(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


TOOLS = [
    {
        "name": "status",
        "description": "Check dependencies, selected input backend, OCR engine, and Sky window detection.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "focus_game",
        "description": "Bring the Sky PC window to the foreground.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean",
                          "description": "Ignore SKY_SKIP_FOCUS and force the window to the foreground."}
            },
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key in Sky PC. Use WASD for movement, space for jump/fly, F for interaction, Q for honk, Tab for flight mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name such as w, a, s, d, space, f, q, e, tab, enter, or shift-space."},
                "duration_ms": {"type": "integer", "description": "Hold duration in milliseconds.", "default": 80},
                "backend": {"type": "string", "description": "Input backend override: auto, pydirectinput, pyautogui, gamepad, or arduino.", "default": "auto"},
                "wing_pickup": {"type": "boolean",
                                "description": "Only for the deliberate wing-light auto-pickup path (double-key enabled). Bypasses the wing guard; never set this to press F around a wing light.", "default": False},
            },
            "required": ["key"],
        },
    },
    {
        "name": "set_wing_guard",
        "description": "Report whether wing_light is currently nearby. While active, this server refuses every press_key('f') except wing_pickup=true, so external callers cannot bypass the wingless-account protection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active": {"type": "boolean", "description": "True = wing_light detected nearby; F must be blocked."},
            },
            "required": ["active"],
        },
    },
    {
        "name": "open_chat",
        "description": "Short-tap the Sky chat key. Use this to test whether Enter opens chat; keep duration short so Enter does not become voice/chat-hold behavior.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Chat key, usually enter.", "default": "enter"},
                "duration_ms": {"type": "integer", "description": "Tap duration in milliseconds.", "default": 35},
                "backend": {"type": "string", "description": "Input backend override: auto, pydirectinput, pyautogui, gamepad, or arduino.", "default": "auto"},
            },
        },
    },
    {
        "name": "send_chat",
        "description": "Open Sky chat, paste a message via clipboard, and optionally send it. Supports Chinese and emoji.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Chat message under 240 characters."},
                "open_key": {"type": "string", "description": "Key used to open chat.", "default": "enter"},
                "open_delay_ms": {"type": "integer", "description": "Delay after opening chat before pasting.", "default": 180},
                "assume_open": {"type": "boolean", "description": "Set true when the chat input is already open; no focus click or open key is sent.", "default": False},
                "send": {"type": "boolean", "description": "Set false to paste without pressing Enter.", "default": True},
                "enter_tap_ms": {"type": "integer", "description": "Short Enter tap duration.", "default": 35},
                "backend": {"type": "string", "description": "Input backend override: auto, pydirectinput, pyautogui, gamepad, or arduino.", "default": "auto"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "type_text",
        "description": "Paste text into an already-open Sky chat input and optionally send. Use this when the user manually opened the input box.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Chat message under 240 characters."},
                "send": {"type": "boolean", "description": "Set false to paste without pressing Enter.", "default": True},
                "enter_tap_ms": {"type": "integer", "description": "Short Enter tap duration.", "default": 35},
                "backend": {"type": "string", "description": "Input backend override: auto, pydirectinput, pyautogui, gamepad, or arduino.", "default": "auto"},
                "require_foreground": {"type": "boolean", "description": "Require Sky to already be the foreground window before pasting.", "default": True},
            },
            "required": ["message"],
        },
    },
    {
        "name": "read_screen",
        "description": "Take a screenshot of the Sky window and OCR visible text.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the Sky window and return it as a PNG image.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_songs",
        "description": "List available Sky sheet-music songs (.txt in Sky Music Studio format) "
                       "from the built-in songs/ folder, user_data/songs/, SKY_SONGS_DIR, and an "
                       "auto-detected SkyMusicPlay library. Use to know which songs can be played.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max songs to return (default 300).",
                          "default": 300},
            },
        },
    },
    {
        "name": "play_song",
        "description": "Play a Sky sheet-music song on the in-game instrument (15-key keyboard). "
                       "Runs asynchronously in a background thread; keys are sent through the same "
                       "hardware-first/software-fallback input backend. Requires the instrument to be "
                       "already open in game. The name is matched exactly then by substring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Song name or part of it, e.g. 斑马斑马."},
                "speed": {"type": "number", "description": "Playback speed multiplier, 0.2-4 (default 1).",
                          "default": 1.0},
                "auto_focus": {"type": "boolean", "description": "Bring Sky to foreground first (default true).",
                               "default": True},
                "backend": {"type": "string", "description": "Input backend override: auto, pydirectinput, "
                                                             "pyautogui, or arduino.", "default": "auto"},
                "choice": {"type": "string", "description": "Which version to play when the name matches "
                                                            "several sheets: an ordinal ('1', '第2个') or a "
                                                            "keyword from the version label (e.g. 烛子)."},
                "humanize": {"type": "integer", "description": "0 = exact (default), 1 = slight jitter + "
                                                              "phrase breaths, 2 = stronger (slow songs).",
                             "default": 0},
                "instrument": {"type": "string", "description": "piano / violin / flute / harmonica. "
                                                               "Controls whether notes may be held (piano cannot "
                                                               "sustain). Defaults to the local song setting.",
                               "default": ""},
                "dry_run": {"type": "boolean", "description": "Resolve the sheet and versions only; do not focus the "
                                                              "game and do not play. Used to ask the player which "
                                                              "version before opening the instrument UI.",
                            "default": False},
            },
            "required": ["name"],
        },
    },
    {
        "name": "stop_song",
        "description": "Stop the currently playing sheet-music song immediately.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "song_status",
        "description": "Get the current song playback status, progress, and errors.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class McpServer:
    def __init__(self, controller: PcSkyController):
        self.controller = controller

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "status":
            return {"content": text_content(json.dumps(self.controller.status(), ensure_ascii=False, indent=2))}
        if name == "focus_game":
            return {"content": text_content(json.dumps(
                self.controller.focus_game(force=bool(arguments.get("force", False))),
                ensure_ascii=False, indent=2))}
        if name == "set_wing_guard":
            return {"content": text_content(json.dumps(
                self.controller.set_wing_guard(bool(arguments.get("active", False))),
                ensure_ascii=False))}
        if name == "press_key":
            result = self.controller.press_key(
                arguments["key"],
                arguments.get("duration_ms", 80),
                arguments.get("backend"),
                arguments.get("wing_pickup", False),
            )
            return {"content": text_content(result)}
        if name == "open_chat":
            result = self.controller.open_chat(
                arguments.get("key", "enter"),
                arguments.get("duration_ms", 35),
                arguments.get("backend"),
            )
            return {"content": text_content(result)}
        if name == "send_chat":
            result = self.controller.send_chat(
                arguments["message"],
                arguments.get("open_key", "enter"),
                arguments.get("open_delay_ms", 180),
                arguments.get("assume_open", False),
                arguments.get("send", True),
                arguments.get("enter_tap_ms", 35),
                arguments.get("backend"),
            )
            return {"content": text_content(result)}
        if name == "type_text":
            result = self.controller.type_text(
                arguments["message"],
                arguments.get("send", True),
                arguments.get("enter_tap_ms", 35),
                arguments.get("backend"),
                arguments.get("require_foreground", True),
            )
            return {"content": text_content(result)}
        if name == "read_screen":
            result = self.controller.read_screen()
            try:
                shot = self.controller.screenshot_base64()
                return {"content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)},
                    {"type": "image", "data": shot, "mimeType": "image/png"},
                ]}
            except Exception:
                return {"content": text_content(json.dumps(result, ensure_ascii=False, indent=2))}

        if name == "take_screenshot":
            data = self.controller.screenshot_base64()
            return {"content": [{"type": "image", "data": data, "mimeType": "image/png"}]}

        if name == "list_songs":
            return {"content": text_content(json.dumps(
                self.controller.list_songs(arguments.get("limit", 300)),
                ensure_ascii=False, indent=2))}
        if name == "play_song":
            return {"content": text_content(json.dumps(
                self.controller.play_song(
                    arguments["name"],
                    arguments.get("speed", 1.0),
                    arguments.get("auto_focus", True),
                    arguments.get("backend"),
                    arguments.get("choice"),
                    arguments.get("humanize", 0),
                    arguments.get("instrument") or None,
                    arguments.get("dry_run", False),
                ), ensure_ascii=False, indent=2))}
        if name == "stop_song":
            return {"content": text_content(json.dumps(
                self.controller.stop_song(), ensure_ascii=False, indent=2))}
        if name == "song_status":
            return {"content": text_content(json.dumps(
                self.controller.song_status(), ensure_ascii=False, indent=2))}
        raise SkyError(f"Unknown tool: {name}")

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": SERVER_INFO,
                    },
                }
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
            if method == "tools/call":
                params = message.get("params") or {}
                result = self.handle_tool_call(params.get("name", ""), params.get("arguments") or {})
                result.setdefault("isError", False)
                return {"jsonrpc": "2.0", "id": msg_id, "result": result}
            if method == "ping":
                return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": text_content(f"Error: {exc}"), "isError": True},
            }


def run_stdio(server: McpServer) -> None:
    log("starting stdio transport")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = server.handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def require_authorized(handler: BaseHTTPRequestHandler, config: ServerConfig) -> bool:
    if not config.token:
        return True
    auth = handler.headers.get("Authorization", "")
    expected = f"Bearer {config.token}"
    if auth == expected:
        return True
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(json.dumps({"error": "missing or invalid bearer token"}).encode("utf-8"))
    return False


def _server_already_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """探测本机端口是否已有 MCP 在监听（能连上即说明有实例）。

    防止重复启动：Windows 下 ThreadingHTTPServer 的 SO_REUSEADDR 会让两个进程都成功绑定
    同一端口，随后入站请求被随机分发，而只有一个进程真正占着硬件，表现为动作时好时坏。
    """
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    try:
        fam = socket.AF_INET6 if ":" in probe_host else socket.AF_INET
    except Exception:
        fam = socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((probe_host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def run_http(server: McpServer, config: ServerConfig) -> None:
    if config.host not in LOCAL_HOSTS and not config.token:
        raise SystemExit("Refusing to bind a remote HTTP server without --token.")
    if _server_already_listening(config.port, config.host):
        raise SystemExit(
            f"端口 {config.port} 上已有 MCP 实例在运行（可能是旧窗口没关、或重复启动）。\n"
            "本实例主动退出，避免两个 MCP 同时抢硬件/分流请求。请先关掉旧的 MCP 窗口再启动。"
        )
    server.controller._detect_ocr_name()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            # 2026-11-01 P0：/health 也要鉴权（以前不鉴权，任何本机进程都能探到服务信息）
            if not require_authorized(self, config):
                return
            body = json.dumps({
                "status": "ok",
                "server": SERVER_INFO,
                "ocr": server.controller._detect_ocr_name(),
                "ocr_device": server.controller._ocr_device,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not require_authorized(self, config):
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                message = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
                return
            response = server.handle(message)
            if response is None:
                self.send_response(204)
                self.end_headers()
                return
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args):
            log(fmt % args)

    httpd = ThreadingHTTPServer((config.host, config.port), Handler)
    log(f"starting HTTP transport at http://{config.host}:{config.port}")
    if config.token:
        log("HTTP bearer token is enabled")
    httpd.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP server for controlling PC Sky.")
    parser.add_argument("--http", nargs="?", const="true", default=None, help="Run HTTP JSON-RPC transport. Optional legacy form: --http 9800.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host. Use 0.0.0.0 for LAN clients such as Polaris.")
    parser.add_argument("--port", type=int, default=9800, help="HTTP port.")
    parser.add_argument("--token", default=os.environ.get("SKY_MCP_TOKEN"), help="Bearer token for HTTP POST requests.")
    parser.add_argument("--print-token", action="store_true", help="Generate and print a random token, then exit.")
    parser.add_argument("--window-title", default=os.environ.get("SKY_WINDOW_TITLE"), help="Window title substring for Sky.")
    parser.add_argument("--monitor", type=int, default=int(os.environ.get("SKY_MONITOR", "1")), help="Monitor index fallback for screenshots.")
    parser.add_argument("--input-backend", choices=["auto", "pyautogui", "pydirectinput", "gamepad", "arduino"], default=os.environ.get("SKY_INPUT_BACKEND", "auto"))
    parser.add_argument("--screenshot-scale", type=float, default=float(os.environ.get("SKY_SCREENSHOT_SCALE", "1.0")))
    parser.add_argument("--screenshot-max-width", type=int, default=int(os.environ.get("SKY_SCREENSHOT_MAX_WIDTH", "1920")))
    parser.add_argument("--screenshot-max-height", type=int, default=int(os.environ.get("SKY_SCREENSHOT_MAX_HEIGHT", "1080")))
    parser.add_argument("--serial-port", default=os.environ.get("SKY_SERIAL_PORT"), help="COM port for Arduino keyboard, e.g. COM9.")
    args = parser.parse_args()
    if args.http and args.http != "true":
        args.port = int(args.http)
    args.http = bool(args.http)
    return args


def _resolve_mcp_token(config: ServerConfig) -> str:
    """HTTP 鉴权 token 的解析顺序（2026-11-01 P0：不再用写死的 1234）：
    --token / SKY_MCP_TOKEN → user_data/mcp_token.txt（首次启动自动生成）。
    sky-loop 读同一文件，所以两边天然一致；token 只落本地、不进仓库。"""
    tok = (config.token or "").strip()
    if tok:
        return tok
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "user_data", "mcp_token.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(tok)
        os.replace(tmp, path)
        log("generated random MCP token -> user_data/mcp_token.txt (loop reads same file)")
    except OSError as e:
        log(f"WARN: cannot persist MCP token ({e}); token is memory-only this run")
    return tok


def main() -> None:
    args = parse_args()
    # 注册退出清理：释放所有修饰键，防止键盘卡住
    atexit.register(_release_all_keys)
    if args.print_token:
        print(secrets.token_urlsafe(24))
        return
    config = ServerConfig(
        host=args.host,
        port=args.port,
        token=args.token,
        allow_unsafe_http=False,
        window_title=args.window_title,
        monitor=args.monitor,
        input_backend=args.input_backend,
        screenshot_scale=args.screenshot_scale,
        screenshot_max_width=args.screenshot_max_width,
        screenshot_max_height=args.screenshot_max_height,
        serial_port=args.serial_port,
    )
    config.token = _resolve_mcp_token(config)
    controller = PcSkyController(config)
    # 启动硬件热插拔守护：插上板子自动用硬件，没插/拔出自动退回软件模拟，
    # 每 30 秒检测、插回自动切回。显式选软件/手柄后端时不启动。
    controller.start_hardware_monitor()
    server = McpServer(controller)
    if args.http:
        run_http(server, config)
    else:
        run_stdio(server)


if __name__ == "__main__":
    main()
