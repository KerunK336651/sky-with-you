# -*- coding: utf-8 -*-
"""
光遇内存聊天读取器
通过 ReadProcessMemory 扫描光遇进程内存，提取聊天消息JSON。
只读，不修改，不注入。

消息JSON格式：
{"sender_id":"...","msg_id":"...","msg":"内容","ch":"local","result":"no_receiver"}
"""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

# Toolhelp32 函数原型（64位安全）
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32First.restype = wintypes.BOOL
kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32Next.restype = wintypes.BOOL
kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

# sender_id -> 玩家名字 的映射文件
UUID_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'user_data', 'mem_uuid_map.json')


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__a1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__a2", wintypes.DWORD),
    ]


def _find_sky_pid():
    """找到光遇主进程PID（线程数最多的那个）。"""
    import subprocess
    try:
        # 用 tasklist 找所有 Sky.exe 的PID
        r = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq Sky.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=5
        )
        pids = []
        for line in r.stdout.strip().split('\n'):
            line = line.strip()
            if not line or 'Sky.exe' not in line:
                continue
            parts = line.split('","')
            if len(parts) >= 2:
                try:
                    pids.append(int(parts[1].strip('"')))
                except ValueError:
                    pass
        if not pids:
            return None
        if len(pids) == 1:
            return pids[0]
        # 多个进程时，用线程数判断主进程
        best_pid, best_threads = pids[0], 0
        for pid in pids:
            try:
                pr = subprocess.run(
                    ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                    capture_output=True, text=True, timeout=5
                )
                # tasklist不直接显示线程数，用wmic
                wr = subprocess.run(
                    ['wmic', 'process', 'where', f'ProcessId={pid}',
                     'get', 'ThreadCount', '/value'],
                    capture_output=True, text=True, timeout=5
                )
                for wl in wr.stdout.split('\n'):
                    if wl.startswith('ThreadCount='):
                        tc = int(wl.split('=')[1].strip())
                        if tc > best_threads:
                            best_threads = tc
                            best_pid = pid
            except Exception:
                pass
        return best_pid
    except Exception:
        return None


def load_uuid_map():
    """加载 sender_id -> 名字 映射。"""
    try:
        with open(UUID_MAP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_uuid_map(m):
    """保存 sender_id -> 名字 映射。"""
    os.makedirs(os.path.dirname(UUID_MAP_FILE), exist_ok=True)
    with open(UUID_MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


class MemoryChatReader:
    """内存聊天读取器，在后台线程中持续扫描。"""

    def __init__(self, on_message, whitelist=None, ai_name="星河"):
        """
        on_message: 回调函数，接收格式化消息列表 ["[名字]内容", ...]
        whitelist: 白名单名字列表，None表示不过滤
        ai_name: AI自己的名字，用于识别自己的消息
        """
        self.on_message = on_message
        self.whitelist = whitelist
        self.ai_name = ai_name
        self.uuid_map = load_uuid_map()
        self.seen_ids = set()
        self.running = False
        self.thread = None
        self.h_proc = None
        self.pid = None
        self.scan_count = 0
        self.error_count = 0
        self.last_scan_time = 0
        self.last_scan_mb = 0

    def start(self):
        """启动后台扫描线程。"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        """停止扫描。"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        self._close_handle()

    def _open_process(self):
        """打开光遇进程，返回是否成功。"""
        self._close_handle()
        self.pid = _find_sky_pid()
        if not self.pid:
            return False
        self.h_proc = kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self.pid
        )
        return bool(self.h_proc)

    def _close_handle(self):
        if self.h_proc:
            kernel32.CloseHandle(self.h_proc)
            self.h_proc = None

    def _scan_once(self):
        """扫描一次内存，返回新消息列表。"""
        mbi = MEMORY_BASIC_INFORMATION()
        addr = 0
        messages = []
        total = 0
        # 复用4MB buffer
        CHUNK = 4 * 1024 * 1024
        buf = ctypes.create_string_buffer(CHUNK)

        while addr < 0x7FFFFFFFFFFF and self.running:
            try:
                if kernel32.VirtualQueryEx(
                    self.h_proc, ctypes.c_void_p(addr),
                    ctypes.byref(mbi), ctypes.sizeof(mbi)
                ) == 0:
                    break
                base = mbi.BaseAddress or 0
                if (mbi.State == 0x1000 and
                        mbi.Protect in (0x04, 0x08, 0x02) and
                        0x1000 <= mbi.RegionSize <= 256 * 1024 * 1024):
                    offset = 0
                    while offset < mbi.RegionSize:
                        chunk_size = min(CHUNK, mbi.RegionSize - offset)
                        br = ctypes.c_size_t()
                        if kernel32.ReadProcessMemory(
                            self.h_proc,
                            ctypes.c_void_p(base + offset),
                            buf, chunk_size, ctypes.byref(br)
                        ):
                            # 只复制实际读取的字节，不用buf.raw（会复制整个buffer）
                            n = br.value
                            total += n
                            # 用memoryview避免复制，搜索时需要bytes
                            data = ctypes.string_at(buf, n)
                            idx = 0
                            while True:
                                idx = data.find(b'"msg":"', idx)
                                if idx == -1:
                                    break
                                s = data.rfind(b'{', max(0, idx - 2048), idx)
                                e = data.find(b'}', idx)
                                if s != -1 and e != -1:
                                    try:
                                        obj = json.loads(
                                            data[s:e + 1].decode('utf-8')
                                        )
                                        if (isinstance(obj, dict) and
                                                'msg' in obj and
                                                'sender_id' in obj and
                                                obj.get('ch') == 'local' and
                                                obj.get('type', 'chat') == 'chat' and
                                                isinstance(obj['msg'], str) and
                                                len(obj['msg']) > 0):
                                            mid = obj.get('msg_id') or \
                                                f"{obj['sender_id']}:{obj['msg']}"
                                            if mid not in self.seen_ids:
                                                self.seen_ids.add(mid)
                                                messages.append(obj)
                                                if len(self.seen_ids) > 500:
                                                    self.seen_ids = set(
                                                        list(self.seen_ids)[-200:]
                                                    )
                                    except Exception:
                                        pass
                                idx = (e + 1) if e != -1 else idx + 7
                            del data
                        offset += CHUNK
                nxt = base + mbi.RegionSize
                if nxt <= addr:
                    break
                addr = nxt
            except MemoryError:
                # 内存不足时跳过当前区域继续
                addr += 0x1000
                import gc
                gc.collect()
            except Exception:
                addr += 0x1000

        return messages, total

    def _format_message(self, obj):
        """把JSON消息格式化为 '[名字]内容' 格式。"""
        sender_id = obj.get('sender_id', '')
        msg = obj.get('msg', '')
        ch = obj.get('ch', '')

        # 只处理本地聊天
        if ch != 'local':
            return None

        # 映射sender_id到名字
        name = self.uuid_map.get(sender_id, '')

        # 如果是未知sender_id，检查消息是否以[名字]开头
        # （OCR格式的消息在内存中可能带名字前缀）
        if not name and msg.startswith('['):
            end = msg.find(']')
            if end > 0:
                name = msg[1:end]
                msg = msg[end + 1:]
                self.uuid_map[sender_id] = name
                save_uuid_map(self.uuid_map)

        # 如果还是未知，用sender_id前8位
        if not name:
            name = sender_id[:8]

        return f"[{name}]{msg}"

    def _run(self):
        """后台扫描主循环。"""
        print("  [MemReader] 线程启动")
        last_pid_check = 0

        while self.running:
            try:
                # 检查进程
                if not self.h_proc or time.time() - last_pid_check > 5:
                    if not self._open_process():
                        time.sleep(2)
                        continue
                    print(f"  [MemReader] 已打开 Sky.exe PID={self.pid}")
                    last_pid_check = time.time()

                t0 = time.time()
                messages, total = self._scan_once()
                elapsed = time.time() - t0
                self.scan_count += 1
                self.last_scan_time = elapsed
                self.last_scan_mb = total

                if messages:
                    formatted = []
                    for obj in messages:
                        line = self._format_message(obj)
                        if line:
                            sid = obj.get('sender_id', '?')[:8]
                            print(f"  [MemReader] 消息: {line}  (sender={sid})")
                            formatted.append(line)

                    if formatted:
                        # 白名单过滤
                        if self.whitelist:
                            filtered = []
                            for line in formatted:
                                # 保留AI自己的消息
                                if f"[{self.ai_name}]" in line:
                                    filtered.append(line)
                                    continue
                                for w in self.whitelist:
                                    if f"[{w}]" in line:
                                        filtered.append(line)
                                        break
                            formatted = filtered

                        if formatted:
                            try:
                                self.on_message(formatted)
                            except Exception as e:
                                print(f"  [MemReader] 回调异常: {e}")

                # 控制扫描频率（连续扫描，微小间隔降低CPU）
                if elapsed < 0.1:
                    time.sleep(0.05)
                elif elapsed < 0.5:
                    time.sleep(0.02)

            except Exception as e:
                self.error_count += 1
                print(f"  [MemReader] 异常: {e}")
                self._close_handle()
                time.sleep(2)

        self._close_handle()
        print("  [MemReader] 线程退出")


def register_name(sender_id, name):
    """手动注册 sender_id -> 名字 映射。"""
    m = load_uuid_map()
    m[sender_id] = name
    save_uuid_map(m)
    print(f"  [MemReader] 已注册: {sender_id[:8]}... -> {name}")


if __name__ == '__main__':
    # 独立测试
    def on_msg(msgs):
        for m in msgs:
            print(f"  >>> {m}")

    reader = MemoryChatReader(on_msg)
    reader.start()
    print("内存聊天读取器已启动，按Ctrl+C停止")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        reader.stop()
