# 家里 R7 7700 CPU 训练说明（v7 28 类）

## 要传回家里的文件
1. `sky_yolo_train_v7_28cls_20261029.zip`（4.28 GB，数据集）
2. `train_yolo.py`（本目录，训练脚本）

## 一、装环境（家里电脑，只需一次）
打开 PowerShell：
```powershell
# 1. 确认有 Python（没有就去 python.org 装 3.11/3.12，勾选 Add to PATH）
py --version

# 2. 装 CPU 版 PyTorch 和 ultralytics
py -m pip install torch torchvision
py -m pip install ultralytics
```

## 二、解压数据集
把数据集 zip 解压到 `D:\sky_yolo_train_v7_28cls`（路径要和脚本里一致；
若解压到别处，训练时把 data.yaml 路径作为参数传入）。
解压后应能看到 `data.yaml`、`images\`、`labels\`。

## 三、开始训练
把 `train_yolo.py` 放到任意目录（建议和数据集同级），在该目录打开 PowerShell：
```powershell
py train_yolo.py
# 若数据集在别的位置：
py train_yolo.py "D:\你的路径\data.yaml"
```
脚本会自动识别没有 GPU、用 CPU 跑（16 线程、batch 8、workers 6），
并自动阻止系统睡眠。

## 四、时间估算（基于学校 E3 实测外推）
- 每轮约 25-32 分钟；跑满 100 轮约 42-53 小时（约 2 天）。
- patience=20 早停，通常 50-70 轮结束，约 21-37 小时。
- **开跑后第一个 epoch 完成，日志里会出现稳定的 `X s/it`**，精确核对：
  - 每轮训练分钟 ≈ 669 × X ÷ 60（再加约 3 分钟 val）
  - 100 轮小时 ≈ 每轮分钟 × 100 ÷ 60
  - 预期 X 约 2.0-2.5。

## 五、中断了怎么办（断电/重启/闪退）
重新打开 PowerShell，在 train_yolo.py 目录运行：
```powershell
py train_yolo.py --resume
```
会从上次的 last.pt 继续，不用从头再来。

## 六、挂机前必做
- 脚本会自动防睡眠；保险起见也可在「电源选项」里把睡眠设为「从不」。
- 设置 Windows 更新的「使用时段」，避免训练中途自动重启。
- 台式机怕断电：有条件接 UPS；没有就靠 `--resume` 兜底。
- 可用向日葵/ToDesk 远程查看进度。

## 七、训练完
- 模型在 `runs\detect\sky_v7_28cls\weights\best.pt`（约 6 MB）。
- 把 `best.pt`、`results.csv`、`confusion_matrix.png` 一起传回学校。
- 部署前先复核 `sky-loop-v7.py` 的类名/ID 映射，再真机验证，别急着覆盖线上模型。
