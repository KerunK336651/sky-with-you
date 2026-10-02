# -*- coding: utf-8 -*-
"""
光遇内存探索工具 - 只读搜索
用法: py mem_scan.py <搜索字符串> [编码]
编码: utf8 (默认) 或 utf16
"""
import sys, ctypes, time
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)

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
        ("AllocationProtect", wintypes.DWORD), ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD), ("__alignment2", wintypes.DWORD),
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
    pid = None
    if kernel32.Process32First(snap, ctypes.byref(pe)):
        while True:
            if b'sky' in pe.szExeFile.lower():
                # 选模块数多的那个（游戏主进程）
                pid = pe.th32ProcessID
            if not kernel32.Process32Next(snap, ctypes.byref(pe)): break
    kernel32.CloseHandle(snap)
    return pid

def scan_memory(h_proc, pattern, max_results=20, context_size=128):
    mbi = MBI()
    addr = 0
    results = []
    total_scanned = 0
    region_count = 0

    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or 0
        if mbi.State == 0x1000 and mbi.Protect in (0x04, 0x02, 0x20, 0x40, 0x08):
            rsize = min(mbi.RegionSize, 16 * 1024 * 1024)
            buf = ctypes.create_string_buffer(rsize)
            br = ctypes.c_size_t()
            if kernel32.ReadProcessMemory(h_proc, ctypes.c_void_p(base), buf, rsize, ctypes.byref(br)):
                data = buf.raw[:br.value]
                total_scanned += len(data)
                region_count += 1
                idx = 0
                while True:
                    idx = data.find(pattern, idx)
                    if idx == -1: break
                    abs_addr = base + idx
                    ctx_start = max(0, idx - context_size)
                    ctx_end = min(len(data), idx + len(pattern) + context_size)
                    context = data[ctx_start:ctx_end]
                    results.append((abs_addr, context, base, mbi.Protect))
                    if len(results) >= max_results:
                        return results, total_scanned, region_count
                    idx += len(pattern)
        nxt = base + mbi.RegionSize
        if nxt <= addr: break
        addr = nxt
    return results, total_scanned, region_count

def format_context(data, pattern, enc):
    """格式化内存上下文，尝试显示可读字符串"""
    lines = []
    # 显示十六进制+ASCII
    for i in range(0, len(data), 32):
        chunk = data[i:i+32]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f"    {hex_str:<96} {ascii_str}")

    # 尝试提取UTF-8字符串
    try:
        text = data.decode('utf-8', errors='replace')
        # 找包含pattern的可读片段
        if enc == 'utf8':
            pat_text = pattern.decode('utf-8', errors='replace')
        else:
            pat_text = pattern.decode('utf-16-le', errors='replace')
        if pat_text in text:
            # 提取周围的可打印字符
            import re
            readable = re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\w\s\.\,\!\?\-\_\:\;\(\)\[\]\{\}\/\\@#\$%\^&\*\+\=]{3,}', text)
            if readable:
                lines.append("    可读字符串片段:")
                for s in readable[:10]:
                    s = s.strip()
                    if len(s) >= 2:
                        lines.append(f"      -> {s}")
    except:
        pass
    return '\n'.join(lines)

if __name__ == '__main__':
    # 输出同时写到文件
    import io
    result_path = r'C:\Users\kgklg\Desktop\sky-with-you-main\mem_scan_result.txt'
    log = open(result_path, 'w', encoding='utf-8')
    class Tee:
        def __init__(self, *fs): self.fs = fs
        def write(self, s):
            for f in self.fs: f.write(s)
        def flush(self):
            for f in self.fs: f.flush()
    sys.stdout = Tee(sys.stdout, log)
    sys.stderr = Tee(sys.stderr, log)

    search_str = sys.argv[1] if len(sys.argv) > 1 else "珂珂"
    enc = sys.argv[2] if len(sys.argv) > 2 else "utf8"

    if enc == 'utf16':
        pattern = search_str.encode('utf-16-le')
    else:
        pattern = search_str.encode('utf-8')

    print(f"搜索: '{search_str}' ({enc}), 模式长度={len(pattern)}字节")

    pid = find_sky_pid()
    if not pid:
        print("未找到Sky.exe，请确认游戏在运行")
        sys.exit(1)
    print(f"Sky.exe PID={pid}")

    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        err = ctypes.get_last_error()
        print(f"OpenProcess失败: {err}（需要管理员权限）")
        sys.exit(1)
    print("进程已打开，开始扫描...\n")

    t0 = time.time()
    results, scanned, regions = scan_memory(h, pattern)
    elapsed = time.time() - t0

    print(f"扫描完成: {scanned/(1024*1024):.1f}MB, {regions}个区域, 耗时{elapsed:.1f}秒")
    print(f"找到 {len(results)} 处匹配\n")

    for i, (addr, ctx, base, prot) in enumerate(results):
        prot_names = {0x02:'RO', 0x04:'RW', 0x08:'WC', 0x20:'RX', 0x40:'RWX'}
        print(f"=== 匹配 #{i+1} @ 0x{addr:X} (区域基址=0x{base:X}, {prot_names.get(prot, hex(prot))}) ===")
        print(format_context(ctx, pattern, enc))
        print()

    kernel32.CloseHandle(h)
    log.close()
