# -*- coding: utf-8 -*-
"""
光遇内存聊天监控器 v3 - 高速扫描
只扫描RW堆区域，跳过模块/贴图区域
"""
import sys, ctypes, time, json
from ctypes import wintypes
from datetime import datetime

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD),
    ]

def find_sky_pid():
    class PE32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260),
        ]
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    pe = PE32(); pe.dwSize = ctypes.sizeof(PE32)
    best_pid, best_threads = None, 0
    if kernel32.Process32First(snap, ctypes.byref(pe)):
        while True:
            if b'sky' in pe.szExeFile.lower() and pe.cntThreads > best_threads:
                best_threads = pe.cntThreads
                best_pid = pe.th32ProcessID
            if not kernel32.Process32Next(snap, ctypes.byref(pe)): break
    kernel32.CloseHandle(snap)
    return best_pid

def scan_fast(h_proc, seen_ids):
    """只扫描RW堆区域，跳过模块和大区域"""
    mbi = MBI()
    addr = 0
    messages = []
    total = 0
    pattern = b'"msg":"'

    while addr < 0x7FFFFFFFFFFF:
        try:
            if kernel32.VirtualQueryEx(h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            base = mbi.BaseAddress or 0
            # MEM_COMMIT=0x1000, PAGE_READWRITE=0x04, PAGE_WRITECOPY=0x08
            # Type: MEM_PRIVATE=0x20000（堆）, MEM_MAPPED=0x40000
            if (mbi.State == 0x1000 and mbi.Protect in (0x04, 0x08, 0x02) and
                0x1000 <= mbi.RegionSize <= 64*1024*1024):
                rsize = min(mbi.RegionSize, 16 * 1024 * 1024)
                buf = ctypes.create_string_buffer(rsize)
                br = ctypes.c_size_t()
                if kernel32.ReadProcessMemory(h_proc, ctypes.c_void_p(base), buf, rsize, ctypes.byref(br)):
                    data = buf.raw[:br.value]
                    total += len(data)
                    idx = 0
                    while True:
                        idx = data.find(pattern, idx)
                        if idx == -1: break
                        start = data.rfind(b'{', max(0, idx - 1024), idx)
                        end = data.find(b'}', idx)
                        if start != -1 and end != -1:
                            try:
                                obj = json.loads(data[start:end+1].decode('utf-8', errors='strict'))
                                if isinstance(obj, dict) and 'msg' in obj and 'msg_id' in obj:
                                    mid = obj['msg_id']
                                    if mid not in seen_ids:
                                        seen_ids.add(mid)
                                        messages.append(obj)
                            except:
                                pass
                        idx = (end + 1) if end != -1 else idx + len(pattern)
            nxt = base + mbi.RegionSize
            if nxt <= addr: break
            addr = nxt
        except:
            addr += 0x1000
    return messages, total

def main():
    log_path = r'C:\Users\kgklg\Desktop\sky-with-you-main\mem_monitor_log.txt'
    log = open(log_path, 'a', encoding='utf-8')
    def out(s):
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        line = f'[{ts}] {s}'
        print(line)
        log.write(line + '\n'); log.flush()

    out("=== 监控启动 v3 (高速堆扫描) ===")
    pid = find_sky_pid()
    if not pid: out("未找到Sky.exe"); return
    out(f"PID={pid}")
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h: out(f"OpenProcess失败: {ctypes.get_last_error()}"); return
    out("已打开，开始监控...")

    seen_ids = set()
    count = 0
    try:
        while True:
            t0 = time.time()
            msgs, total = scan_fast(h, seen_ids)
            elapsed = time.time() - t0
            for m in msgs:
                sid = m.get('sender_id','?')[:8]
                out(f"消息! [{m.get('ch','?')}] {sid}: {m.get('msg','')}")
            count += 1
            if count % 50 == 0:
                out(f"心跳 #{count} 已见{len(seen_ids)}条 扫描{total//1024}KB {elapsed*1000:.0f}ms")
            time.sleep(max(0, 0.1 - elapsed))
    except KeyboardInterrupt:
        out("停止")
    finally:
        kernel32.CloseHandle(h)
        log.close()

if __name__ == '__main__':
    main()
