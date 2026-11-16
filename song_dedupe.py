# -*- coding: utf-8 -*-
"""song_dedupe.py —— 曲库去重扫描（**只读**，不动任何文件）。

用途：把一批"新歌"和现有曲库比对，找出**内容完全相同**的谱，避免导入后
同一份谱占两个坑（点歌时还会被当成"两个版本"逼玩家选）。

判定口径（重要）：
  * 重复 = **内容指纹相同**：bpm + 音符序列 [(time, key)] 规范化后的 sha1。
    也就是"同一份谱"，只是文件名不同。
  * **同歌名但改编不同（时长/音符数/bpm 不同）不算重复**，绝不删——
    这正是项目"多版本必问玩家"要保护的东西。
  * 加密谱（isEncrypted=true）或坏 JSON → 归到"无法解析"，不参与比对。

用法：
    py song_dedupe.py --library songs --scan <目录1> [<目录2> ...]
    py song_dedupe.py --library songs --scan <目录> --report dedupe_report.txt
    py song_dedupe.py --library songs --scan <目录> --list-dup     # 打印重复明细

输出四类：
    A 可导入（曲库里没有这份指纹，批内也不重复）
    B 与曲库重复（指纹已在 --library 里）
    C 批内互相重复（同一份谱在批里出现多次，只留一个）
    D 无法解析（坏 JSON / 加密谱 / 没有音符）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sky_song_player as sp  # noqa: E402


def fingerprint(path: str | Path) -> tuple[str | None, dict]:
    """算一首谱的内容指纹。返回 (指纹 or None, 元信息)。

    指纹 = sha1(bpm + 规范化音符序列)，与文件名/展示名无关。
    None 表示"没有可用的音符"（加密、坏 JSON、空谱都归这一类）。
    """
    p = Path(path)
    info = {"name": "", "bpm": 0, "notes": 0, "reason": ""}
    try:
        text = sp._read_text_auto(p)
        payload = json.loads(text)
    except Exception as e:
        info["reason"] = "bad_json:%s" % type(e).__name__
        return None, info

    meta = {}
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        meta = payload[0]
    elif isinstance(payload, dict):
        meta = payload
    info["name"] = str(meta.get("name") or sp.display_name(p.name))
    info["bpm"] = int(meta.get("bpm") or 0)
    if meta.get("isEncrypted"):
        info["reason"] = "encrypted"
        return None, info

    notes = sp._extract_song_notes(payload)
    pairs = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        t, k = n.get("time"), n.get("key")
        if t is None or k is None:
            continue
        try:
            pairs.append((int(float(t)), str(k)))
        except (TypeError, ValueError):
            continue
    info["notes"] = len(pairs)
    if not pairs:
        info["reason"] = "no_notes"
        return None, info

    pairs.sort()
    h = hashlib.sha1()
    h.update(str(info["bpm"]).encode())
    h.update(b"|")
    h.update((",".join("%d:%s" % (t, k) for t, k in pairs)).encode("utf-8", "replace"))
    return h.hexdigest(), info


def collect(dir_list: list[str]) -> list[Path]:
    """递归收集目录下的 .txt 曲谱。"""
    out: list[Path] = []
    for d in dir_list:
        root = Path(d)
        if not root.exists():
            print("  [跳过] 目录不存在：%s" % d)
            continue
        out.extend(sorted(root.rglob("*.txt")))
    return out


def scan(paths: list[Path], label: str) -> dict[str, dict]:
    """扫描一批文件，返回 {指纹: {path, info}}；重复的指保留第一个。"""
    index: dict[str, dict] = {}
    bad = 0
    for i, p in enumerate(paths, 1):
        fp, info = fingerprint(p)
        if fp is None:
            bad += 1
            index.setdefault("__bad__%d" % i, {"path": str(p), "info": info, "fp": None})
            continue
        if fp in index:
            index[fp].setdefault("dups", []).append(str(p))
        else:
            index[fp] = {"path": str(p), "info": info, "fp": fp}
        if i % 2000 == 0:
            print("    已扫描 %d/%d ..." % (i, len(paths)), flush=True)
    return index


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default="songs", help="现有曲库目录（可多个，逗号分隔）")
    ap.add_argument("--scan", nargs="+", required=True, help="要比对的新曲目目录")
    ap.add_argument("--report", default="dedupe_report.txt")
    ap.add_argument("--list-dup", action="store_true", help="在终端打印重复明细")
    ap.add_argument("--limit-print", type=int, default=30)
    a = ap.parse_args()

    lib_dirs = [x for x in a.library.split(",") if x.strip()]

    print("=" * 78)
    print("曲库去重扫描（只读）")
    print("=" * 78)
    lib_paths = collect(lib_dirs)
    print("  现有曲库：%s -> %d 个 .txt" % (", ".join(lib_dirs), len(lib_paths)))
    lib_index = scan(lib_paths, "library")
    lib_fps = {k: v for k, v in lib_index.items() if not k.startswith("__bad__")}
    print("  曲库可用指纹：%d 个（无法解析 %d 个）"
          % (len(lib_fps), len(lib_index) - len(lib_fps)))

    new_paths = collect(a.scan)
    print("  待比对新曲：%s -> %d 个 .txt" % (", ".join(a.scan), len(new_paths)))
    if not new_paths:
        print("  没有找到任何 .txt，退出。")
        return 1

    print("  扫描新曲中 ...", flush=True)
    new_index = scan(new_paths, "new")

    ok_new = {k: v for k, v in new_index.items() if not k.startswith("__bad__")}
    bad_new = {k: v for k, v in new_index.items() if k.startswith("__bad__")}

    cat_a, cat_b, cat_c = [], [], []
    for fp, rec in ok_new.items():
        if fp in lib_fps:
            cat_b.append((fp, rec, lib_fps[fp]))
        elif rec.get("dups"):
            cat_c.append((fp, rec))
        else:
            cat_a.append((fp, rec))

    # ---- 正确口径：内容(fingerprint) vs 文件 ----
    def files_of(rows):
        """每个 (fp, rec[, lib_rec]) 代表 1 个内容 —— 文件数要把 dups 一起算上。

        注意取 x[1]：cat_b 的条目是 (fp, rec, lib_rec)，x[-1] 会拿到曲库那条记录。
        """
        return sum(1 + len(x[1].get("dups") or []) for x in rows)

    n_files = len(new_paths)
    n_parse = sum(1 + len(r.get("dups") or []) for r in ok_new.values())
    n_distinct = len(ok_new)
    n_collapse = n_parse - n_distinct
    n_bad = len(bad_new)

    reason_hist: dict[str, int] = {}
    for r in bad_new.values():
        key = (r["info"].get("reason") or "?").split(":")[0]
        reason_hist[key] = reason_hist.get(key, 0) + 1

    lines = []
    lines.append("曲库去重扫描报告（只读，未改动任何文件）")
    lines.append("现有曲库：%s（%d 个 .txt，%d 个可用指纹，%d 个无法解析）"
                 % (", ".join(lib_dirs), len(lib_paths), len(lib_fps),
                    len(lib_index) - len(lib_fps)))
    lines.append("待比对：%s（%d 个 .txt）" % (", ".join(a.scan), n_files))
    lines.append("")
    lines.append("── 总账 ──")
    lines.append("  待比对文件总数                 : %d" % n_files)
    lines.append("  其中可解析                     : %d" % n_parse)
    lines.append("    去重后**不同内容**           : %d" % n_distinct)
    lines.append("    同内容重复（可收起）         : %d" % n_collapse)
    lines.append("  其中无法解析                   : %d" % n_bad)
    lines.append("")
    lines.append("── 分类（按内容）──")
    lines.append("  B 曲库已有这份内容（导入即重复）: %d 个内容 / %d 个文件"
                 % (len(cat_b), files_of(cat_b)))
    lines.append("  C 批内重复（曲库没有）          : %d 个内容 / %d 个文件"
                 % (len(cat_c), files_of(cat_c)))
    lines.append("  A **全新可用**                  : %d 个内容 / %d 个文件"
                 % (len(cat_a), files_of(cat_a)))
    lines.append("")
    if reason_hist:
        lines.append("── 无法解析的原因 ──")
        for k, v in sorted(reason_hist.items(), key=lambda kv: -kv[1]):
            lines.append("  %-22s %d" % (k, v))
        lines.append("")

    def dump(title, rows):
        lines.append("=" * 70)
        lines.append(title)
        lines.append("=" * 70)
        for row in rows:
            lines.append("  " + row)
        lines.append("")

    dump("A 全新可用 —— 曲库里没有这份内容，批内也只出现一次",
         ["%s  (bpm=%d, %d 音符)" % (Path(r["path"]).name, r["info"]["bpm"], r["info"]["notes"])
          for _f, r in cat_a])
    dump("B 曲库已有 —— 导入会变成重复",
         ["%s  ==  曲库 %s" % (Path(r["path"]).name, Path(lr["path"]).name)
          for _f, r, lr in cat_b[:400]])
    dump("C 批内重复 —— 曲库没有，但批里出现了多次（每组留一个即可）",
         ["%s  ~~  %s" % (Path(r["path"]).name, " + ".join(Path(d).name for d in r.get("dups") or []))
          for _f, r in cat_c[:400]])
    dump("D 无法解析",
         ["%s  [%s]" % (Path(r["path"]).name, r["info"].get("reason"))
          for r in list(bad_new.values())[:400]])

    Path(a.report).write_text("\n".join(lines), encoding="utf-8")
    print()
    print("  ── 总账 ──")
    print("  待比对文件总数        : %d" % n_files)
    print("  可解析                : %d" % n_parse)
    print("    不同内容            : %d" % n_distinct)
    print("    同内容重复          : %d" % n_collapse)
    print("  无法解析              : %d" % n_bad)
    print()
    print("  B 曲库已有            : %d 个内容 / %d 个文件" % (len(cat_b), files_of(cat_b)))
    print("  C 批内重复            : %d 个内容 / %d 个文件" % (len(cat_c), files_of(cat_c)))
    print("  A **全新可用**        : %d 个内容 / %d 个文件" % (len(cat_a), files_of(cat_a)))
    if reason_hist:
        print("  无法解析原因          : " + ", ".join("%s=%d" % kv for kv in
                                                    sorted(reason_hist.items(), key=lambda kv: -kv[1])))
    print()
    print("  报告已写入：" + a.report)

    if a.list_dup:
        print()
        print("  --- B 前 %d 条 ---" % a.limit_print)
        for _f, r, lr in cat_b[:a.limit_print]:
            print("    %s  ==  曲库 %s" % (Path(r["path"]).name, Path(lr["path"]).name))
        print("  --- C 前 %d 条 ---" % a.limit_print)
        for _f, r in cat_c[:a.limit_print]:
            print("    %s  ==  %s" % (Path(r["path"]).name,
                                       " && ".join(Path(d).name for d in r.get("dups") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
