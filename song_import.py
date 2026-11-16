# -*- coding: utf-8 -*-
"""song_import.py —— 把一批新曲导入 user_data/songs/（**内容去重**，默认不碰 songs/）。

为什么默认写 user_data/songs/：README 明确"本仓库不打包曲谱库"（社区转录谱版权归
原作者/转录者），songs/ 会进 git；user_data/ 是 gitignore 的个人目录，播放器同样扫描。

去重口径与 song_dedupe.py 一致：内容指纹（bpm + 音符序列）。
库指纹索引会缓存到 user_data/_song_lib_fp.json，改库后可 --refresh-index 重建。

用法：
    py song_import.py --scan <目录> [--target user_data/songs] [--dry-run]
    py song_import.py --scan <目录> --include-encrypted   # 默认跳过加密/坏谱
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sky_song_player as sp  # noqa: E402
from song_dedupe import fingerprint  # noqa: E402


def build_library_index(lib_dirs: list[str], cache: Path, refresh: bool) -> set[str]:
    if cache.exists() and not refresh:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            print("  复用库指纹缓存：%d 个（改过曲库请加 --refresh-index）" % len(data))
            return set(data)
        except Exception:
            pass
    print("  扫描现有曲库 ...", flush=True)
    fps: set[str] = set()
    total = 0
    for d in lib_dirs:
        for p in Path(d).rglob("*.txt"):
            total += 1
            fp, _info = fingerprint(p)
            if fp:
                fps.add(fp)
            if total % 3000 == 0:
                print("    已扫 %d ..." % total, flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(sorted(fps)), encoding="utf-8")
    print("  曲库 %d 个文件 -> %d 个可用指纹（已缓存）" % (total, len(fps)))
    return fps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", nargs="+", required=True)
    ap.add_argument("--library", default="songs")
    ap.add_argument("--target", default=os.path.join("user_data", "songs"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-index", action="store_true")
    a = ap.parse_args()

    lib_dirs = [x for x in a.library.split(",") if x.strip()]
    target = Path(a.target)
    cache = Path("user_data") / "_song_lib_fp.json"

    print("=" * 74)
    print("曲库导入（内容去重）%s" % ("［演练，不写盘］" if a.dry_run else ""))
    print("=" * 74)
    lib_fps = build_library_index(lib_dirs, cache, a.refresh_index)

    # 收集待导入文件
    files: list[Path] = []
    for d in a.scan:
        files.extend(sorted(Path(d).rglob("*.txt")))
    print("  待扫描新曲：%d 个" % len(files))

    # 按指纹分组（同内容只导一次）
    groups: dict[str, list[Path]] = {}
    bad = 0
    for i, p in enumerate(files, 1):
        fp, _info = fingerprint(p)
        if fp is None:
            bad += 1
            continue
        groups.setdefault(fp, []).append(p)
        if i % 2000 == 0:
            print("    已算指纹 %d/%d ..." % (i, len(files)), flush=True)

    new_fps = [fp for fp in groups if fp not in lib_fps]
    dup_fps = [fp for fp in groups if fp in lib_fps]
    print("  不同内容 %d 个：库已有 %d，**可新增 %d**（无法解析 %d）"
          % (len(groups), len(dup_fps), len(new_fps), bad))

    if a.dry_run:
        print()
        for fp in sorted(new_fps, key=lambda f: groups[f][0].name):
            print("    会导入: %s" % groups[fp][0].name)
        return 0

    target.mkdir(parents=True, exist_ok=True)
    ok = skipped = failed = 0
    for fp in sorted(new_fps, key=lambda f: groups[f][0].name):
        src = groups[fp][0]
        dst = target / src.name
        k = 2
        while dst.exists():
            dst = target / ("%s~%d%s" % (src.stem, k, src.suffix))
            k += 1
        try:
            shutil.copy2(src, dst)
            # 导入后立刻用真解析器验一遍，防止把坏谱塞进曲库
            sp.load_sheet(dst)
            ok += 1
            print("  + %s" % dst.name)
        except Exception as e:
            failed += 1
            try:
                dst.unlink()
            except OSError:
                pass
            print("  ! %s 导入失败并已回滚（%s）" % (src.name, type(e).__name__))

    print()
    print("  导入成功 %d 首，失败 %d 首，跳过（库已有）%d 首" % (ok, failed, len(dup_fps)))
    print("  目标目录：%s" % target.resolve())

    # 导入后自检：用播放器同一条链路确认能找到
    print()
    print("  自检（find_song 抽查前 5 首）：")
    for fp in sorted(new_fps, key=lambda f: groups[f][0].name)[:5]:
        nm = sp.display_name(groups[fp][0].name)
        hit = sp.find_song(nm, [str(target)])
        print("    %-32s -> %s" % (nm, (hit or {}).get("name") or "找不到"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
