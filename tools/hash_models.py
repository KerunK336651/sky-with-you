#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hash_models.py —— 计算（或校验）目录内所有 .pt 权重的 SHA256，生成 SHA256SUMS.txt。

用法：
    # 生成/刷新 SHA256SUMS.txt（扫描该目录下所有 .pt，按文件名排序）
    py tools/hash_models.py "C:\\path\\to\\release_dir"

    # 校验：读目录里的 SHA256SUMS.txt，逐个比对实际哈希
    py tools/hash_models.py "C:\\path\\to\\release_dir" --check

发布 Release 前，先用 stamp_model.py 嵌入署名，再用本脚本刷新 SHA256SUMS.txt。
"""

import argparse
import hashlib
import os
import sys


def sha256_of(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def cmd_generate(directory: str) -> int:
    pts = sorted(f for f in os.listdir(directory) if f.lower().endswith(".pt"))
    if not pts:
        print(f"[警告] {directory} 下没有 .pt 文件")
        return 1
    lines = []
    for name in pts:
        digest = sha256_of(os.path.join(directory, name))
        lines.append(f"{digest}  {name}")
        print(f"{digest}  {name}")
    out = os.path.join(directory, "SHA256SUMS.txt")
    # 与 Linux sha256sum 格式一致：每行 "<hash>  <name>"，末尾换行
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n已写入 {out}（{len(lines)} 个文件）")
    return 0


def cmd_check(directory: str) -> int:
    sums = os.path.join(directory, "SHA256SUMS.txt")
    if not os.path.isfile(sums):
        print(f"[错误] 找不到 {sums}")
        return 2
    ok = True
    with open(sums, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            digest, _, name = line.partition("  ")
            name = name.strip()
            p = os.path.join(directory, name)
            if not os.path.isfile(p):
                print(f"[缺失] {name}")
                ok = False
                continue
            actual = sha256_of(p)
            if actual == digest.strip():
                print(f"[OK]   {name}")
            else:
                print(f"[不符] {name}\n        期望 {digest.strip()}\n        实际 {actual}")
                ok = False
    print("\n全部一致。" if ok else "\n存在不一致，请勿发布。")
    return 0 if ok else 3


def main():
    ap = argparse.ArgumentParser(description="生成/校验 .pt 权重的 SHA256SUMS.txt")
    ap.add_argument("directory", help="存放 .pt 与 SHA256SUMS.txt 的目录")
    ap.add_argument("--check", action="store_true", help="校验模式（默认是生成模式）")
    args = ap.parse_args()
    if not os.path.isdir(args.directory):
        print(f"[错误] 不是目录: {args.directory}")
        sys.exit(2)
    sys.exit(cmd_check(args.directory) if args.check else cmd_generate(args.directory))


if __name__ == "__main__":
    main()
