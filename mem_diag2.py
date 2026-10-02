# -*- coding: utf-8 -*-
"""诊断v2：扩大搜索范围"""
import ctypes, json, time
from ctypes import wintypes
import subprocess

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

r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Sky.exe', '/FO', 'CSV', '/NH'],
                   capture_output=True, text=True, timeout=5)
pids = []
for line in r.stdout.strip().split('\n'):
    if 'Sky.exe' in line:
        parts = line.split('","')
        if len(parts) >= 2:
            pids.append(int(parts[1].strip('"')))

out = open(r'C:\Users\kgklg\Desktop\sky-with-you-main\mem_diag2.txt', 'w', encoding='utf-8')
def log(s):
    out.write(s + '\n'); out.flush()

log(f"PIDs: {pids}")

# 选线程数最多的PID
import os
best_pid = pids[0] if pids else None
best_tc = 0
for pid in pids:
    try:
        wr = subprocess.run(['wmic', 'process', 'where', f'ProcessId={pid}', 'get', 'ThreadCount', '/value'],
                          capture_output=True, text=True, timeout=5)
        for wl in wr.stdout.split('\n'):
            if wl.startswith('ThreadCount='):
                tc = int(wl.split('=')[1].strip())
                log(f"PID={pid} threads={tc}")
                if tc > best_tc:
                    best_tc = tc
                    best_pid = pid
    except: pass

log(f"主进程 PID={best_pid}")
h = kernel32.OpenProcess(0x0010 | 0x0400, False, best_pid)
if not h:
    log(f"OpenProcess失败: {ctypes.get_last_error()}")
    out.close()
    exit()

# 扫描所有可读区域，不限大小
mbi = MBI()
addr = 0
total = 0
regions = 0
msg_count = 0
sender_count = 0
samples = []
t0 = time.time()

while addr < 0x7FFFFFFFFFFF:
    if kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
        break
    base = mbi.BaseAddress or 0
    # 所有MEM_COMMIT的可读区域，不限大小
    if mbi.State == 0x1000 and mbi.Protect in (0x02, 0x04, 0x08, 0x20, 0x40):
        rsize = min(mbi.RegionSize, 64*1024*1024)  # 单次最多读64MB
        buf = ctypes.create_string_buffer(rsize)
        br = ctypes.c_size_t()
        if kernel32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, rsize, ctypes.byref(br)):
            data = buf.raw[:br.value]
            total += len(data)
            regions += 1

            # 搜索 "msg":"
            idx = 0
            while True:
                idx = data.find(b'"msg":"', idx)
                if idx == -1: break
                s = data.rfind(b'{', max(0, idx-2048), idx)
                e = data.find(b'}', idx)
                if s != -1 and e != -1:
                    try:
                        obj = json.loads(data[s:e+1].decode('utf-8'))
                        if isinstance(obj, dict) and 'msg' in obj:
                            msg_count += 1
                            if len(samples) < 20:
                                samples.append(f"msg JSON @ {hex(base+s)} type={hex(mbi.Type)} prot={hex(mbi.Protect)} size={mbi.RegionSize}: {json.dumps(obj, ensure_ascii=False)[:200]}")
                    except: pass
                idx = (e+1) if e != -1 else idx+7

            # 搜索 sender_id
            idx = data.find(b'sender_id')
            if idx != -1:
                sender_count += 1
                context = data[max(0,idx-50):idx+200]
                try:
                    samples.append(f"sender_id @ {hex(base+idx)}: {context.decode('utf-8', errors='replace')[:200]}")
                except: pass

    nxt = base + mbi.RegionSize
    if nxt <= addr: break
    addr = nxt

elapsed = time.time() - t0
log(f"扫描{regions}个区域 {total/1024/1024:.0f}MB {elapsed:.2f}秒")
log(f"找到 msg JSON: {msg_count} 处")
log(f"找到 sender_id: {sender_count} 处")
log("样本:")
for s in samples[:20]:
    log(f"  {s}")
log("完成")
out.close()
