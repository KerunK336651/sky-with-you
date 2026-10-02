# -*- coding: utf-8 -*-
"""快速诊断：扫描内存中的聊天消息"""
import ctypes, json, time, sys
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD),
    ]

import subprocess
r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Sky.exe', '/FO', 'CSV', '/NH'],
                   capture_output=True, text=True, timeout=5)
pids = []
for line in r.stdout.strip().split('\n'):
    if 'Sky.exe' in line:
        parts = line.split('","')
        if len(parts) >= 2:
            pids.append(int(parts[1].strip('"')))

out = open(r'C:\Users\kgklg\Desktop\sky-with-you-main\mem_diag.txt', 'w', encoding='utf-8')
def log(s):
    print(s)
    out.write(s + '\n'); out.flush()

log(f"找到 Sky.exe PIDs: {pids}")

for pid in pids:
    h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not h:
        log(f"PID={pid} OpenProcess失败: {ctypes.get_last_error()}")
        continue
    log(f"PID={pid} 已打开，开始扫描...")

    mbi = MBI()
    addr = 0
    total = 0
    regions = 0
    msg_count = 0
    samples = []
    t0 = time.time()

    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or 0
        if mbi.State == 0x1000 and mbi.Protect in (0x04, 0x08, 0x02) and 0x1000 <= mbi.RegionSize <= 64*1024*1024:
            rsize = min(mbi.RegionSize, 16*1024*1024)
            buf = ctypes.create_string_buffer(rsize)
            br = ctypes.c_size_t()
            if kernel32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, rsize, ctypes.byref(br)):
                data = buf.raw[:br.value]
                total += len(data)
                regions += 1
                idx = 0
                while True:
                    idx = data.find(b'"msg":"', idx)
                    if idx == -1: break
                    s = data.rfind(b'{', max(0, idx-1024), idx)
                    e = data.find(b'}', idx)
                    if s != -1 and e != -1:
                        try:
                            obj = json.loads(data[s:e+1].decode('utf-8'))
                            if isinstance(obj, dict) and 'msg' in obj and 'msg_id' in obj:
                                msg_count += 1
                                if len(samples) < 10:
                                    samples.append({
                                        'msg': obj.get('msg','')[:50],
                                        'sender': obj.get('sender_id','')[:8],
                                        'ch': obj.get('ch',''),
                                        'addr': hex(base + s),
                                        'type': hex(mbi.Type),
                                        'protect': hex(mbi.Protect),
                                    })
                        except: pass
                    idx = (e+1) if e != -1 else idx+7
        nxt = base + mbi.RegionSize
        if nxt <= addr: break
        addr = nxt

    elapsed = time.time() - t0
    log(f"PID={pid}: 扫描{regions}个区域 {total/1024/1024:.0f}MB {elapsed:.2f}秒")
    log(f"  找到 {msg_count} 条消息JSON")
    for s in samples:
        log(f"  {s}")
    kernel32.CloseHandle(h)

log("\n完成")
out.close()
