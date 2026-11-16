#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stamp_model.py —— 给训练好的 YOLO 权重嵌入“作者署名”（软水印 / attribution）。

为什么需要它：
    权重发布出去后，无法在技术上阻止别人下载、转发或再训练。但我们可以在 .pt 文件
    内部写入作者、来源、许可等信息。它防君子不防小人（懂的人重新 torch.save 就能剥离），
    但绝大多数直接下载转发的人不会清理，用 torch.load 一打开就能看到原作者，是低成本、
    不影响推理的署名标记。真正的归属证明仍要靠 GitHub 首发时间戳、Zenodo 存档、训练日志
    与数据集等证据链（见 PROJECT_HANDOFF.md「模型发布与归属」）。

它做什么：
    只在 checkpoint 顶层加一个 `_attribution` 字段，绝不改动 model 权重、类别名或其它内容，
    所以不影响 ultralytics 的 YOLO(path) 加载、推理和再训练。

用法：
    py tools/stamp_model.py models/yolo/sky_v6_22cls_best.pt
    py tools/stamp_model.py a.pt b.pt c.pt --release v1.0

以后每次训练出新模型，先跑本脚本嵌入署名，再算 SHA256、发布 Release（流程见交接文档）。
"""

import argparse
import datetime
import os
import sys

import torch

# ── 固定的署名信息（以后换模型不用改；若改了仓库/作者，再统一改这里）─────────────
AUTHOR = "KerunK336651 (珂珂)"
PROJECT = "sky-with-you 光遇 AI 伙伴"
SOURCE_URL = "https://github.com/KerunK336651/sky-with-you"
WEIGHTS_LICENSE = "CC BY-NC-SA 4.0（非商用、署名、相同方式共享；详见 MODEL_WEIGHTS_LICENSE.md）"
CODE_LICENSE = "GPL-3.0"
NOTICE = (
    "本权重由作者独立采集、标注游戏截图并训练。转载请保留本署名与许可；禁止商用；"
    "基于本权重再训练或衍生的模型，须以相同协议开放。游戏《光·遇》画面与内容版权归"
    " thatgamecompany / 网易(TGC) 所有，本模型仅用于学习交流，不得用于违反游戏条款或损害他人的用途。"
)


def build_attribution(pt_path: str, release: str) -> dict:
    attr = {
        "author": AUTHOR,
        "project": PROJECT,
        "source": SOURCE_URL,
        "release": release,
        "model_file": os.path.basename(pt_path),
        "weights_license": WEIGHTS_LICENSE,
        "code_license": CODE_LICENSE,
        "trained_by": AUTHOR,
        "released_at": datetime.date.today().isoformat(),
        "notice": NOTICE,
    }
    return attr


def check_pt_safe(pt_path: str, anywhere: bool = False) -> str | None:
    """最小安全守卫（2026-11-13 加）。返回 None 表示可继续，否则返回拒绝原因。

    为什么需要它：`torch.load(..., weights_only=False)` 会**执行 pickle 里的任意可调用对象**
    （ultralytics 的 .pt 是含模型类的 dict，确实必须 False）。这与"把 .pt 交给陌生人"没有区别，
    所以至少要拦住"明显不是权重的东西"和"手滑指向了别的目录的文件"：
      1) 后缀必须是 .pt；
      2) 头部必须是 torch 的 zip 容器（`PK\x03\x04`）；老式 pickle（`\x80\x02`）放行但提示；
      3) 默认只允许项目目录内（`--anywhere` 可显式放开）。
    真正的信任边界是"这个文件从哪来"，本函数只防呆，不防恶意。
    """
    if not os.path.isfile(pt_path):
        return "文件不存在"
    if not pt_path.lower().endswith(".pt"):
        return "不是 .pt 文件"
    try:
        with open(pt_path, "rb") as f:
            head = f.read(4)
    except OSError as e:
        return "读不了：%s" % e
    if head[:4] != b"PK\x03\x04":
        if head[:2] == b"\x80\x02":
            print(f"[警告] {os.path.basename(pt_path)} 是老式 pickle 格式，"
                  f"weights_only=False 会执行其中的代码，请确认来源可信")
        else:
            return "头部不是 torch/zip 容器（%r），拒绝加载" % head
    if not anywhere:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        real = os.path.realpath(pt_path)
        if os.path.commonpath([os.path.realpath(root), real]) != os.path.realpath(root):
            return "路径不在项目目录内；确认可信请加 --anywhere"
    return None


def stamp(pt_path: str, release: str, anywhere: bool = False) -> None:
    if not os.path.isfile(pt_path):
        print(f"[跳过] 找不到文件: {pt_path}")
        return
    why = check_pt_safe(pt_path, anywhere=anywhere)
    if why:
        print(f"[拒绝] {pt_path}：{why}")
        return
    # ultralytics 保存的 .pt 是含模型类的 dict，必须 weights_only=False
    ckpt = torch.load(pt_path, map_location="cpu", weights_only=False)
    if not isinstance(ckpt, dict):
        print(f"[跳过] {pt_path} 不是 checkpoint dict（顶层类型 {type(ckpt).__name__}），无法嵌入")
        return

    model = ckpt.get("model")
    nc = getattr(model, "nc", None)
    names = getattr(model, "names", None)
    old = ckpt.get("_attribution")
    if old:
        print(f"[提示] {os.path.basename(pt_path)} 已存在署名，将覆盖："
              f"原作者={old.get('author')} 发布={old.get('release')}")

    attr = build_attribution(pt_path, release)
    if nc is not None:
        attr["classes"] = nc
    ckpt["_attribution"] = attr
    torch.save(ckpt, pt_path)

    print(f"[完成] {os.path.basename(pt_path)}  classes={nc}  release={release}")
    if isinstance(names, dict):
        print(f"       类别: {names}")


def main():
    ap = argparse.ArgumentParser(description="给 YOLO 权重嵌入作者署名（软水印）")
    ap.add_argument("weights", nargs="+", help="一个或多个 .pt 路径")
    ap.add_argument("--release", default="v1.0", help="发布版本标签，默认 v1.0")
    ap.add_argument("--anywhere", action="store_true",
                    help="允许处理项目目录之外的路径（默认只允许项目内）")
    args = ap.parse_args()
    for p in args.weights:
        stamp(p, args.release, anywhere=args.anywhere)
    print("\n已处理。下一步：重算 SHA256（py tools/hash_models.py 或手工 Get-FileHash）再发布。")


if __name__ == "__main__":
    main()
