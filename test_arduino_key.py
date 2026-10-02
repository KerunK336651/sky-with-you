# -*- coding: utf-8 -*-
"""
Arduino 按键测试脚本
用法：
  1. 确保 Arduino Pro Micro 已连接到电脑（COM5）
  2. 运行此脚本：py test_arduino_key.py
  3. 脚本会通过串口发送按键指令，观察 Arduino 是否响应
"""
import time
import serial


def _find_port():
    """自动探测 Arduino（VID 2341/1B4F/239A），失败回退到手动指定。"""
    manual = "COM4"  # 自动探测失败时改这里
    try:
        from serial.tools import list_ports
        known = {0x2341, 0x1B4F, 0x239A}
        for p in list_ports.comports():
            if p.vid in known or "arduino" in (p.description or "").lower():
                return p.device
    except Exception:
        pass
    return manual


PORT = _find_port()
BAUDRATE = 115200

print("=" * 50)
print("  Arduino 按键测试")
print("=" * 50)

# 测试 1: 连接 Arduino
print(f"\n[1/4] 连接 Arduino ({PORT})...")
try:
    ser = serial.Serial(PORT, BAUDRATE, timeout=5)
    print(f"  已连接到 {PORT}")
    # 等待 Arduino 复位并发送就绪信息
    ready = ser.readline().decode().strip()
    print(f"  Arduino 就绪: {ready}")
except Exception as e:
    print(f"  [错误] 连接失败: {e}")
    print("  请检查：")
    print("    1. Arduino 是否已连接到电脑")
    print(f"    2. 串口号是否正确（当前是 {PORT}）")
    print("    3. 设备管理器中查看端口号")
    input("\n按回车键退出...")
    exit(1)

# 测试 2: 发送 RELEASE 指令
print("\n[2/4] 发送 RELEASE 指令（松开所有键）...")
try:
    ser.write(b"RELEASE\n")
    resp = ser.readline().decode().strip()
    print(f"  响应: {resp}")
except Exception as e:
    print(f"  [错误] 发送失败: {e}")

# 测试 3: 发送 PING 指令
print("\n[3/4] 发送 PING 指令...")
try:
    ser.write(b"PING\n")
    resp = ser.readline().decode().strip()
    print(f"  响应: {resp}")
except Exception as e:
    print(f"  [错误] 发送失败: {e}")

# 测试 4: 发送 PRESS 指令（按 C 键，键码 99）
print("\n[4/4] 发送 PRESS 指令（按 C 键 150ms）...")
print("  请观察：如果光标在某个输入框中，应该会输入字母 c")
print("  3秒后开始...")
for i in range(3, 0, -1):
    print(f"  {i}...")
    time.sleep(1)

try:
    ser.write(b"PRESS 99 150\n")
    resp = ser.readline().decode().strip()
    print(f"  响应: {resp}")
    if resp == "OK":
        print("  [成功] Arduino 按键正常！")
    else:
        print(f"  [警告] 响应不是 OK，可能固件有问题")
except Exception as e:
    print(f"  [错误] 发送失败: {e}")

ser.close()
print("\n" + "=" * 50)
print("  测试完成！")
print("  如果响应都是 OK，说明 Arduino 正常工作。")
print("  如果没有响应或报错，说明 Arduino 或固件有问题。")
print("=" * 50)
input("\n按回车键退出...")
