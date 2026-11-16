# -*- coding: utf-8 -*-
"""
YOLOv8 训练脚本（GPU 优先，自动回退 CPU；家里 R7 7700 已优化）
用法：
  py train_yolo.py                    # 训练（自动识别 GPU/CPU）
  py train_yolo.py D:\path\data.yaml  # 手动指定 data.yaml
  py train_yolo.py --resume           # 中断后断点续训
完成后：
  runs/detect/sky_v7_28cls/weights/best.pt
"""
import os
import sys
import argparse

# ========== 配置 ==========
DEFAULT_DATA = r"D:\sky_yolo_train_v7_28cls\data.yaml"
MODEL = "yolov8n.pt"
EPOCHS = 100
IMGSZ = 960
PATIENCE = 20
GPU_BATCH = 16
GPU_WORKERS = 8
CPU_BATCH = 8          # CPU 稳妥值（v5 在 R7 实测）
# ==========================


def prevent_sleep():
    """训练期间阻止系统自动睡眠/关屏（不影响手动锁屏）。"""
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        print("[防睡眠] 训练期间已阻止系统自动睡眠")
    except Exception as e:
        print("[防睡眠] 设置失败，请手动在电源选项里关闭睡眠：", e)


def resume_train():
    last = r"runs/detect/sky_v7_28cls/weights/last.pt"
    if not os.path.exists(last):
        print("找不到", last, "，无法续训（确认之前已训练过）。")
        sys.exit(1)
    from ultralytics import YOLO
    print("从 last.pt 断点续训 ...")
    YOLO(last).train(resume=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=DEFAULT_DATA)
    ap.add_argument("--resume", action="store_true", help="断点续训")
    args = ap.parse_args()

    try:
        import torch
    except ImportError:
        print("未安装 PyTorch。CPU 版安装：pip install torch torchvision")
        sys.exit(1)

    print("=" * 50)
    print("PyTorch:", torch.__version__)
    if torch.cuda.is_available():
        device = 0
        batch = GPU_BATCH
        workers = GPU_WORKERS
        amp = True
        print("GPU:", torch.cuda.get_device_name(0))
    else:
        device = "cpu"
        batch = CPU_BATCH
        amp = False  # CPU 无 FP16 张量核心，用 FP32 最稳
        nlogical = os.cpu_count() or 16
        torch.set_num_threads(nlogical)
        nphys = nlogical // 2
        workers = max(2, min(6, nphys - 2))
        print("CPU 训练（无 GPU）")
        print("线程=%d workers=%d batch=%d" % (torch.get_num_threads(), workers, batch))
        prevent_sleep()
    print("=" * 50)

    if args.resume:
        resume_train()
        return

    if not os.path.exists(args.data):
        print("找不到 data.yaml:", args.data)
        print("请确认数据集已解压，或把路径作为参数传入。")
        sys.exit(1)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("正在安装 ultralytics ...")
        os.system("%s -m pip install ultralytics" % sys.executable)
        from ultralytics import YOLO

    YOLO(MODEL).train(
        data=args.data,
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=batch,
        device=device,
        workers=workers,
        amp=amp,
        project="runs/detect",
        name="sky_v7_28cls",
        exist_ok=True,
        patience=PATIENCE,
        save=True,
        plots=True,
        verbose=True,
    )

    print("\n" + "=" * 50)
    print("训练完成！")
    print("best.pt: runs/detect/sky_v7_28cls/weights/best.pt")
    print("=" * 50)


if __name__ == "__main__":
    main()
