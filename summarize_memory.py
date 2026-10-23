# -*- coding: utf-8 -*-
"""把已结束周期的事件日记压缩成分层总结（周记 / 月记 / 年记，记忆金字塔）。

用途：
  - 手动回填：主程序启动时会自动检查补压缩（SKY_SUMMARY_ON_STARTUP），
    本脚本用于手动触发、或在游戏外提前回填。
  - 周记按 ISO 周切分（周一为起点，如 "2026-W41"）；
  - 月记按日历年月（如 "2026-10"）；年记按日历年（如 "2026"）；
  - 只压缩"已结束"的周期（本周/本月/当年不生成）；
  - 幂等：已存在的 period 跳过，可重复执行；压缩后事件层不删除。

用法（在项目根目录）：
    py summarize_memory.py            # 正式执行（需要联网 + key.txt）
    py summarize_memory.py --dry      # 只看会补哪些周期，不调 LLM
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


def main(dry=False):
    events = M.load_diary()
    if not events:
        print("事件日记为空（diary.json 无条目），暂无可压缩内容。")
        return 0
    print(f"事件日记 {len(events)} 条，时间跨度 {events[0].get('time','')} "
          f"→ {events[-1].get('time','')}")
    print(f"已有总结：周记 {len(M.load_weekly())} 篇、"
          f"月记 {len(M.load_monthly())} 篇、年记 {len(M.load_yearly())} 篇\n")

    due = M.due_periods(events=events)
    if due["weekly"]:
        print("待补周记：")
        for k in due["weekly"]:
            wr = M.week_range(k)
            rng = f"（{wr[0]}~{wr[1]}）" if wr else ""
            print(f"  {k} {rng}")
    if due["monthly"]:
        print("待补月记：")
        for k in due["monthly"]:
            print(f"  {k}")
    if due["yearly"]:
        print("待补年记：")
        for k in due["yearly"]:
            print(f"  {k}")
    n_due = len(due["weekly"]) + len(due["monthly"]) + len(due["yearly"])
    if not n_due:
        print("分层记忆无缺口，无需补压缩。")
        return 0

    if dry:
        print(f"\n--dry 模式：共 {n_due} 个周期待补，未调用 LLM。")
        return 0

    client = _make_client()
    print()
    added = M.summarize_due_periods(client, companion_name="星河", events=events)
    print(f"\n压缩完成：周记 +{added['weekly']}、月记 +{added['monthly']}、"
          f"年记 +{added['yearly']}。")
    for level, key in added["items"]:
        print(f"  + {level} {key}")
    return n_due


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
