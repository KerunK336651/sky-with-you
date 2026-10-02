# -*- coding: utf-8 -*-
"""测试管理员权限下能否读取光遇内存"""
import sys, ctypes
from ctypes import wintypes

log = open(r'C:\Users\kgklg\Desktop\sky-with-you-main\memory_test_result.txt', 'w', encoding='utf-8')
class Tee:
    def __init__(self, *files): self.files = files
    def write(self, s):
        for f in self.files: f.write(s)
    def flush(self):
        for f in self.files: f.flush()
sys.stdout = Tee(sys.stdout, log)
sys.stderr = Tee(sys.stderr, log)

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)

# 声明64位兼容的函数签名
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
psapi.EnumProcessModulesEx.restype = wintypes.BOOL
psapi.GetModuleFileNameExA.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPSTR, wintypes.DWORD]
psapi.GetModuleFileNameExA.restype = wintypes.DWORD

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

# 找Sky进程
class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260),
    ]

snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
pe = PROCESSENTRY32(); pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
sky_pids = []
if kernel32.Process32First(snapshot, ctypes.byref(pe)):
    while True:
        if b'sky' in pe.szExeFile.lower():
            sky_pids.append(pe.th32ProcessID)
        if not kernel32.Process32Next(snapshot, ctypes.byref(pe)): break
kernel32.CloseHandle(snapshot)

for pid in sky_pids:
    print(f"\n=== PID={pid} ===")
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print(f"  OpenProcess 失败: {ctypes.get_last_error()}")
        continue
    print(f"  OpenProcess 成功")

    # 枚举模块获取基地址
    LIST_MODULES_ALL = 0x03
    h_mods = (ctypes.c_void_p * 2048)()
    cb = wintypes.DWORD()
    if psapi.EnumProcessModulesEx(h, h_mods, ctypes.sizeof(h_mods), ctypes.byref(cb), LIST_MODULES_ALL):
        n = cb.value // ctypes.sizeof(ctypes.c_void_p)
        print(f"  模块数: {n}")
        # 第一个模块是主程序
        base = h_mods[0]
        name_buf = ctypes.create_string_buffer(260)
        psapi.GetModuleFileNameExA(h, base, name_buf, 260)
        print(f"  主模块: {name_buf.value.decode('gbk', errors='replace')}")
        print(f"  基地址: 0x{base:X}")

        # 读PE头
        buf = ctypes.create_string_buffer(64)
        br = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, 64, ctypes.byref(br))
        if ok and buf.raw[:2] == b'MZ':
            print(f"  PE头读取成功! MZ标记确认, 读取了{br.value}字节")
            # 读PE头偏移
            e_lfanew = ctypes.c_int.from_buffer_copy(buf.raw, 0x3C).value
            print(f"  e_lfanew = 0x{e_lfanew:X}")

            # 读PE签名
            pe_buf = ctypes.create_string_buffer(256)
            kernel32.ReadProcessMemory(h, ctypes.c_void_p(base + e_lfanew), pe_buf, 256, ctypes.byref(br))
            if pe_buf.raw[:4] == b'PE\x00\x00':
                machine = ctypes.c_ushort.from_buffer_copy(pe_buf.raw, 4).value
                n_sections = ctypes.c_ushort.from_buffer_copy(pe_buf.raw, 6).value
                print(f"  PE签名确认! 机器类型=0x{machine:X} ({'x64' if machine==0x8664 else 'x86'}) 节区数={n_sections}")
        else:
            print(f"  读取失败: {ctypes.get_last_error()}")

        # 扫描内存，搜索中文字符串"珂珂"或"星河"的UTF-8/UTF-16编码
        print("\n  === 搜索内存中的字符串 ===")
        search_terms = [
            ("珂珂(UTF-8)", "珂珂".encode('utf-8')),
            ("星河(UTF-8)", "星河".encode('utf-8')),
            ("珂珂(UTF-16LE)", "珂珂".encode('utf-16-le')),
            ("星河(UTF-16LE)", "星河".encode('utf-16-le')),
        ]

        class MBI(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
            ]

        mbi = MBI()
        addr = 0
        found = {name: 0 for name, _ in search_terms}
        scanned = 0
        while addr < 0x7FFFFFFFFFFF:
            if kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            base_addr = mbi.BaseAddress or 0
            # MEM_COMMIT=0x1000, 可读属性
            if mbi.State == 0x1000 and mbi.Protect in (0x04, 0x02, 0x20, 0x40, 0x08):
                region_size = min(mbi.RegionSize, 8 * 1024 * 1024)
                rbuf = ctypes.create_string_buffer(region_size)
                br2 = ctypes.c_size_t()
                if kernel32.ReadProcessMemory(h, ctypes.c_void_p(base_addr), rbuf, region_size, ctypes.byref(br2)):
                    data = rbuf.raw[:br2.value]
                    scanned += len(data)
                    for name, term in search_terms:
                        idx = 0
                        while True:
                            idx = data.find(term, idx)
                            if idx == -1: break
                            found[name] += 1
                            if found[name] <= 3:
                                abs_addr = base_addr + idx
                                context = data[max(0,idx-20):idx+len(term)+20]
                                print(f"    [{name}] @ 0x{abs_addr:X}: ...{context!r}...")
                            idx += len(term)
            next_addr = base_addr + mbi.RegionSize
            if next_addr <= addr: break
            addr = next_addr

        print(f"\n  扫描了 {scanned/(1024*1024):.1f} MB 内存")
        for name, count in found.items():
            print(f"    {name}: {count} 处")
    else:
        print(f"  EnumProcessModules失败: {ctypes.get_last_error()}")

    kernel32.CloseHandle(h)

log.close()
