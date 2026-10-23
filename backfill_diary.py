# -*- coding: utf-8 -*-
"""把存量对话（memory.json 的 raw_turns，带时间戳）回溯整理成首批事件日记。

用途：日记层上线时冷启动 —— 让星河不是从零开始记，而是把已有的历史对话
      （目前 2026-10-15 ~ 10-19 共 80 条）整理成带时间戳的事件日记。

用法（在项目根目录）：
    py backfill_diary.py            # 正式执行（需要联网 + key.txt）
    py backfill_diary.py --dry      # 只看会整理哪些批，不调 LLM

行为：
  - 分批（每批 20 条）调用 LLM，输出 "时间|事件|关键词" 行；
  - 与已有 diary.json 去重（同事件不重复入库），可重复执行；
  - 结果原子写入 user_data/diary.json（自动留 .bak）。
"""
import importlib.util
import os
import sys

import core.memory as M


def _make_client():
    """复用主程序的 LLM 配置（provider/key/model 保持一致）。

    与 start_loop.bat 保持一致：未显式设置环境变量时默认 DeepSeek
    （key.txt 里放的是 DeepSeek key；不设会走 openrouter 默认导致 401）。
    """
    os.environ.setdefault("SKY_LLM_PROVIDER", "deepseek")
    os.environ.setdefault("SKY_LLM_MODEL", "deepseek-flash")
    spec = importlib.util.spec_from_file_location("loop", "sky-loop-v7.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["loop"] = mod
    spec.loader.exec_module(mod)
    print(f"  LLM: {mod.LLM_PROVIDER} / {mod.MODEL}")
    return mod.LLMClient(
        provider=mod.LLM_PROVIDER,
        api_key=mod.API_KEY,
        base_url=mod.DEFAULT_BASE_URL,
        model=mod.MODEL,
    )


def main(dry=False, batch=20):
    mem = M.load_memory()
    turns = mem.get("raw_turns", [])
    if not turns:
        print("没有存量对话（raw_turns 为空），无需回溯。")
        return 0
    existing = {d.get("event") for d in M.load_diary()}
    print(f"存量对话 {len(turns)} 条，时间跨度 {turns[0].get('time','')} → {turns[-1].get('time','')}")
    print(f"已有日记 {len(existing)} 条（重复事件会自动跳过）\n")

    if dry:
        for i in range(0, len(turns), batch):
            chunk = turns[i:i + batch]
            print(f"  批次{i // batch + 1}: {len(chunk)} 条 "
                  f"({chunk[0].get('time','')} ~ {chunk[-1].get('time','')})")
        print("\n--dry 模式，未调用 LLM。")
        return 0

    client = _make_client()
    added = 0
    for i in range(0, len(turns), batch):
        chunk = turns[i:i + batch]
        print(f"  批次{i // batch + 1}: 整理 {len(chunk)} 条 ...")
        try:
            out = client.chat(
                M.build_backfill_prompt(chunk, "星河"),
                temperature=0.2, max_tokens=600)
        except Exception as e:
            print(f"    LLM 调用失败: {e}")
            continue
        items = M.parse_backfill_output(out)
        fresh = [it for it in items if it["event"] not in existing]
        if not fresh:
            print("    （无新增）")
            continue
        for it in fresh:
            existing.add(it["event"])
        M.save_diary(M.load_diary() + fresh)
        added += len(fresh)
        for it in fresh:
            print(f"    + {it['time']} | {it['event']} | {','.join(it['tags'])}")

    print(f"\n回溯完成，新增 {added} 条日记；diary.json 现有 {len(M.load_diary())} 条。")
    size = os.path.getsize(M.DIARY_FILE) if os.path.exists(M.DIARY_FILE) else 0
    print(f"文件大小：{size / 1024:.1f} KB")
    return added


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
