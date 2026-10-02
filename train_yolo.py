# -*- coding: utf-8 -*-
"""
YOLOv8 训练脚本
用法：
  1. 修改下面的 DATASET_PATH 为你的数据集路径
  2. 运行: py train_yolo.py
  3. 训练完成后模型在 runs/detect/train/weights/best.pt
"""
import os
import sys

# ========== 配置 ==========
DATASET_PATH = r"D:\yolo_dataset_v3"   # 改成你的数据集解压路径
MODEL_SIZE = "yolov8n.pt"            # nano版，最快，适合实时
EPOCHS = 100                          # 训练轮数
IMGSZ = 640                            # 图片尺寸
BATCH = 8                              # 批次大小（CPU训练调小一点）
# ==========================

def main():
    # 检查数据集路径
    data_yaml = os.path.join(DATASET_PATH, "data.yaml")
    if not os.path.exists(data_yaml):
        print(f"错误: 找不到 {data_yaml}")
        print(f"请确认数据集已解压到 {DATASET_PATH}")
        print(f"目录下应该有 data.yaml、train/、valid/、test/ 文件夹")
        sys.exit(1)
    
    print("=" * 50)
    print("YOLOv8 训练")
    print("=" * 50)
    print(f"数据集: {DATASET_PATH}")
    print(f"模型: {MODEL_SIZE}")
    print(f"轮数: {EPOCHS}")
    print(f"图片尺寸: {IMGSZ}")
    print(f"批次大小: {BATCH}")
    print("=" * 50)
    
    # 检查 ultralytics 是否安装
    try:
        from ultralytics import YOLO
    except ImportError:
        print("正在安装 ultralytics...")
        os.system(f"{sys.executable} -m pip install ultralytics")
        from ultralytics import YOLO
    
    # 加载模型
    print(f"\n加载模型 {MODEL_SIZE}...")
    model = YOLO(MODEL_SIZE)
    
    # 训练
    print("\n开始训练...")
    print("（CPU训练可能需要1-2小时，请耐心等待）")
    print()
    
    results = model.train(
        data=data_yaml,
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=BATCH,
        device="cpu",           # CPU训练（没有N卡）
        workers=2,               # 数据加载线程数
        project="runs/detect",
        name="train",
        exist_ok=True,
        patience=20,             # 20轮没提升就早停
        save=True,
        plots=True,              # 生成训练曲线图
        verbose=True,
    )
    
    print("\n" + "=" * 50)
    print("训练完成！")
    print(f"最佳模型: runs/detect/train/weights/best.pt")
    print(f"最后模型: runs/detect/train/weights/last.pt")
    print("=" * 50)
    print("\n下一步:")
    print("  1. 运行 py test_yolo.py 测试模型效果")
    print("  2. 把 best.pt 集成到主程序")

if __name__ == "__main__":
    main()
