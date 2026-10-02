# -*- coding: utf-8 -*-
"""
将 negatives/ 目录中的负样本加入YOLO训练集
负样本 = 没有任何目标的图片，标签文件为空（0字节）
"""
import os
import shutil
import random

BASE = os.path.dirname(os.path.abspath(__file__))
NEG_DIR = os.path.join(BASE, "negatives")
DATASET = r"D:\yolo_dataset_v3"
TRAIN_IMG = os.path.join(DATASET, "train", "images")
TRAIN_LBL = os.path.join(DATASET, "train", "labels")
VALID_IMG = os.path.join(DATASET, "valid", "images")
VALID_LBL = os.path.join(DATASET, "valid", "labels")

VALID_RATIO = 0.1  # 10%作为验证集

def main():
    if not os.path.isdir(NEG_DIR):
        print(f"错误: 未找到负样本目录 {NEG_DIR}")
        print("请先运行 collect_negatives.py 采集负样本")
        return

    exts = ('.png', '.jpg', '.jpeg', '.bmp')
    files = [f for f in os.listdir(NEG_DIR) if f.lower().endswith(exts)]

    if not files:
        print("负样本目录为空，请先采集截图")
        return

    print(f"找到 {len(files)} 张负样本")
    print(f"训练集目录: {TRAIN_IMG}")
    print(f"验证集目录: {VALID_IMG}")
    print()

    random.seed(42)
    random.shuffle(files)

    n_valid = max(1, int(len(files) * VALID_RATIO))
    valid_files = set(files[:n_valid])

    train_count = 0
    valid_count = 0

    for fname in files:
        src = os.path.join(NEG_DIR, fname)
        is_valid = fname in valid_files

        if is_valid:
            dst_img = os.path.join(VALID_IMG, fname)
            dst_lbl = os.path.join(VALID_LBL, os.path.splitext(fname)[0] + '.txt')
            valid_count += 1
        else:
            dst_img = os.path.join(TRAIN_IMG, fname)
            dst_lbl = os.path.join(TRAIN_LBL, os.path.splitext(fname)[0] + '.txt')
            train_count += 1

        shutil.copy2(src, dst_img)
        # 创建空标签文件（负样本的关键）
        with open(dst_lbl, 'w') as f:
            pass  # 空文件

    print(f"已添加 {train_count} 张到训练集")
    print(f"已添加 {valid_count} 张到验证集")
    print()
    print("负样本标签文件为空（0字节），YOLO会学习这些图片中没有目标")
    print("现在可以运行 train_yolo.py 重新训练")

if __name__ == "__main__":
    main()
