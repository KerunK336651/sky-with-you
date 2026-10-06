# models/ocr —— PP-OCRv5 ONNX 模型（OCR 可选升级件）

这三个文件是 **PaddleOCR 官方 PP-OCRv5 mobile 模型**转换来的 ONNX，供
`sky-loop-v7.py` / `panel_detector.py` 在 `SKY_OCR_MODEL=v5` 时使用。
不放这三个文件也能跑：缺失时 `make_ocr_engine()` 会自动回退 RapidOCR 自带的 PP-OCRv3。

## 文件与完整性

| 文件 | 大小 | sha256 |
|---|---|---|
| `v5_det.onnx` | 4,767,099 B | `fd4d9b7b107ef959c5358ae567ce1ce1c6b8d61f01b6102ea42f763babb2ebda` |
| `v5_rec.onnx` | 16,518,562 B | `3080c7ee62a4455bf2a46601928ed3c1d165ce3b3b613f4a86582c6f1c2be58a` |
| `ppocr_keys_v5.txt` | 74,012 B | `d1979e9f794c464c0d2e0b70a7fe14dd978e9dc644c0e71f14158cdf8342af1b` |

校验：`Get-FileHash models\ocr\* -Algorithm SHA256`（或 `sha256sum`）。

## 来源与许可

- 上游：**PaddleOCR**（百度）→ https://github.com/PaddlePaddle/PaddleOCR
  - 原始模型：`PP-OCRv5_mobile_det` / `PP-OCRv5_mobile_rec`（官方推理模型）
- 转换工具：**Paddle2ONNX** → https://github.com/PaddlePaddle/Paddle2ONNX
- 许可：**Apache License 2.0**（PaddleOCR 与 Paddle2ONNX 均为 Apache-2.0，可再分发；
  再分发时请保留本说明与许可信息）
- 复现转换（官方教程的写法，逐目录执行）：

  ```bash
  paddle2onnx --model_dir ./ \
      --model_filename inference.json \
      --params_filename inference.pdiparams \
      --save_file model.onnx
  onnxslim model.onnx slim.onnx          # 精简后的 slim.onnx 即本目录的 v5_*.onnx
  ```

> 说明：本目录只含 PaddleOCR 官方模型，**不含任何游戏数据、不含任何第三方软件自有的模型**。

## 为什么还需要 `ppocr_keys_v5.txt`

RapidOCR 的识别器优先从 ONNX 的 metadata 里读字符表（`metadata['character']`）；
但 PaddleOCR 官方导出的 ONNX **不含这份 metadata**，此时 RapidOCR 会退回读
`config['Rec']['keys_path']` 指向的字典文件。所以 v5 路线必须同时给出字典。

- 内容：PP-OCRv5 官方字符表 **18383 字**（可由官方 `ppocrv5_dict.txt` 取得，也可从
  官方 `inference.yml` 的 `PostProcess.character_dict` 取出）
- 校验关系：`18383 字 + blank + 空格 = 18385` = `v5_rec.onnx` 的输出类别数。
  长度对不上会直接报错或识别出乱码，`test_ocr_engine.py` 就断言了这个等式。

## 怎么用

```bat
:: 一键（推荐）：用 v5 跑主循环
start_loop_v5.bat

:: 或手动设环境变量
set SKY_OCR_MODEL=v5     :: v3（默认）/ v5
set SKY_OCR_DEVICE=auto  :: auto（默认，有 DirectML 就走 GPU）/ dml / cpu
```

启动时每个 OCR 引擎会打一行"实际生效"日志，用它核对是否真的用上了 v5 / GPU：

```
  [PD] OCR 引擎: PP-OCRv5 + DmlExecutionProvider
```

（判断依据是模型指纹——`PP-OCRv5` = rec 输出 18385 类、`PP-OCRv3` = 6625 类，
所以即使请求 v5 却被静默回退成 v3，这一行也会如实显示 v3。）

## 实测（家里机器：AMD RX 5600 XT，14 张真机聊天帧）

| 组合 | 均耗时 | 解析出的有效消息 |
|---|---|---|
| PP-OCRv3 + CPU（旧默认） | 329~386 ms/帧 | 73 |
| PP-OCRv3 + DirectML | 130 ms/帧 | 73 |
| **PP-OCRv5 + DirectML** | **143~149 ms/帧** | **78** |
| PP-OCRv5 + CPU | 217 ms/帧 | 78 |

- GPU 化提速约 2.5~3 倍，且 v3 的识别结果与 CPU 完全一致（零行为变化）。
- v5 的主要收益是**行切分更干净**：v3 有时会把两条不同说话人的消息粘成一行
  （导致说话人/内容错位），v5 能正确拆开，解析出的消息数 73 → 78。
- 想回退：`SKY_OCR_DEVICE=cpu`（不动模型）或 `SKY_OCR_MODEL=v3`（不动设备），
  也可整体删掉本目录——模型缺失会自动回退 v3。

## 自检

```bat
py test_ocr_engine.py     :: 断言"真加载了哪套模型""真走了哪个设备"以及三条回退路径
```
