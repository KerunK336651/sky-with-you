# -*- coding: utf-8 -*-
"""连续扫描测试 - 不sleep，记录扫描耗时和新消息"""
import sys, ctypes, time, json
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

pid = 33768
h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
if not h:
    print(f"OpenProcess失败: {ctypes.get_last_error()}")
    sys.exit(1)

log = open(r'C:\Users\kgklg\Desktop\sky-with-you-main\mem_continuous_log.txt', 'w', encoding='utf-8')
def out(s):
    print(s)
    log.write(s + '\n'); log.flush()

out(f"已打开 PID={pid}")
out("开始连续扫描，请在游戏里发消息...")
out("按Ctrl+C停止\n")

seen = set()
scan_count = 0
t_start = time.time()

try:
    while True:
        try:
            t0 = time.time()
            mbi = MBI()
            addr = 0
            total = 0
            new_msgs = []

            while addr < 0x7FFFFFFFFFFF:
                try:
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
                                            mid = obj['msg_id']
                                            if mid not in seen:
                                                seen.add(mid)
                                                new_msgs.append(obj)
                                    except: pass
                                idx = (e+1) if e != -1 else idx+7
                    nxt = base + mbi.RegionSize
                    if nxt <= addr: break
                    addr = nxt
                except Exception as e:
                    addr += 0x1000

            elapsed = time.time() - t0
            scan_count += 1
            ts = time.strftime('%H:%M:%S')
            for m in new_msgs:
                out(f"[{ts}] 新消息! [{m.get('ch','?')}] {m.get('sender_id','?')[:8]}: {m.get('msg','')}")
            if scan_count % 10 == 0:
                rate = total/1024/1024/elapsed if elapsed > 0 else 0
                out(f"[{ts}] 扫描#{scan_count} {total/1024/1024:.0f}MB {elapsed:.2f}s ({rate:.0f}MB/s) 已见{len(seen)}条")
        except Exception as e:
            out(f"[{time.strftime('%H:%M:%S')}] 扫描异常: {e}")
            time.sleep(1)

except KeyboardInterrupt:
    out(f"\n停止，共扫描{scan_count}次，{time.time()-t_start:.0f}秒")
finally:
    kernel32.CloseHandle(h)
    log.close()
