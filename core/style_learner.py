# -*- coding: utf-8 -*-
"""Style learning for sky-with-you.
Ported from sky-companion ocr_agent.py style-learning methods.

When the personality prompt contains keywords like 病恋/病娇/虚恋/抖音/参考风格,
this module searches the web for public style references and asks the LLM
to distill a speaking-style prompt (with safety filters built in).

2026-10-19 新增 player_style_profile()：从玩家真实对话（raw_turns）统计说话
习惯（句长/语气词/问句比例/高频口头禅），生成给 AI 看的本地画像。零联网零 LLM，
每条消息实时计算（最多80条素材，开销可忽略）。
"""
import hashlib
import re
from collections import Counter


_TONE_CHARS = "呀啦嘛哈嘿嗯哦欸诶呜哇呐捏咯咧噻嘛"


def _clean_text(txt):
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", txt or "").strip()


def style_prompt_key(personality_prompt):
    """SHA256 hash (first 24 hex chars) of the personality prompt.

    Used as a cache key so we don't re-learn the same style repeatedly.
    """
    raw = (personality_prompt or "").strip()
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def player_style_profile(raw_turns, min_turns=8, limit=60):
    """从玩家真实消息统计说话习惯，生成一段给 AI 看的画像文本。

    Args:
        raw_turns: memory.json 的 raw_turns 列表（每项 {"player":..., "companion":...}）。
        min_turns: 素材少于此条数时返回空（样本太少统计不可靠）。
        limit:     最多取最近多少条玩家消息做统计。

    Returns:
        str: 画像文本（句长习惯/语气词/问句比例/高频口头禅），或 ""。
    """
    try:
        texts = []
        for t in (raw_turns or []):
            s = str(t.get("player", "") or "").strip() if isinstance(t, dict) else ""
            if len(s) >= 2:
                texts.append(s)
        texts = texts[-limit:]
        if len(texts) < min_turns:
            return ""

        # 句长习惯
        lens = [len(re.sub(r"\s", "", t)) for t in texts]
        avg = sum(lens) / len(lens)
        if avg <= 10:
            length_desc = "说话很短，常常一两个字一句"
        elif avg <= 20:
            length_desc = "短句为主"
        elif avg <= 35:
            length_desc = "句子中等长度"
        else:
            length_desc = "喜欢一次说比较长的话"

        parts = [f"TA习惯{length_desc}（平均约{avg:.0f}字）"]

        # 语气词偏好
        tone_counter = {}
        for t in texts:
            for ch in set(t):
                if ch in _TONE_CHARS:
                    tone_counter[ch] = tone_counter.get(ch, 0) + 1
        top_tones = [c for c, n in sorted(tone_counter.items(), key=lambda x: -x[1]) if n >= 3][:4]
        if top_tones:
            parts.append("常带语气词「" + "」「".join(top_tones) + "」")

        # 问句 / 感叹比例
        q_ratio = sum(1 for t in texts if "？" in t or "?" in t) / len(texts)
        if q_ratio >= 0.3:
            parts.append(f"爱用问句（约{int(q_ratio * 100)}%）")
        excl_ratio = sum(1 for t in texts if "！" in t or "!" in t) / len(texts)
        if excl_ratio >= 0.25:
            parts.append("情绪外露，爱用感叹号")

        # 高频口头禅（中文二字组，过滤语气词字）
        bg = Counter()
        for t in texts:
            for i in range(len(t) - 1):
                b = t[i:i + 2]
                if all("\u4e00" <= c <= "\u9fff" for c in b) and not any(c in _TONE_CHARS for c in b):
                    bg[b] += 1
        phrases = [w for w, n in bg.most_common(12) if n >= 3][:3]
        if phrases:
            parts.append("常说「" + "」「".join(phrases) + "」")

        if len(parts) <= 1:
            return ""
        return "；".join(parts) + "。回复时贴合TA的节奏就好，不用刻意模仿TA的用词。"
    except Exception:
        return ""


def style_search_queries(personality_prompt):
    """Generate web search queries based on personality prompt keywords.

    Returns a list of up to 2 query strings, or empty list if no style
    keywords are detected.
    """
    prompt = personality_prompt or ""
    clean = _clean_text(prompt)
    if not clean:
        return []
    queries = []
    if any(x in clean for x in ("病恋", "病娇", "占有欲", "疯批", "偏执")):
        queries.extend([
            "病恋 病娇 恋爱 说话风格 文案 抖音",
            "病娇 占有欲 情感表达 文案 语气",
        ])
    if any(x in clean for x in ("虚恋", "恋人", "暧昧", "甜")):
        queries.append("虚拟恋爱 甜宠 说话风格 文案")
    if any(x in clean for x in ("人机恋", "克劳德", "Claude", "活人感", "陪伴")):
        queries.append("Claude 人机恋 活人感 恋爱陪伴 说话风格")
    if any(x in clean for x in ("虐恋", "拉扯", "破碎感")):
        queries.append("虐恋 拉扯感 说话风格 文案")
    if "抖音" in clean and not queries:
        queries.append(prompt[:50] + " 说话风格 抖音")
    if any(x in clean for x in ("参考", "学习", "模仿", "风格")) and not queries:
        queries.append(prompt[:50] + " 说话风格")
    return queries[:2]


def build_style_prompt(personality_prompt, search_blocks, llm_call):
    """Ask the LLM to distill a speaking-style reference from search results.

    Args:
        personality_prompt: the user's original personality prompt.
        search_blocks: list of "搜索：query\\nresults" strings.
        llm_call: callable taking (prompt, temperature=, max_tokens=) -> str.

    Returns:
        str: a distilled style prompt (≤120 chars), or empty string on failure.
    """
    try:
        prompt = (
            "你是光遇AI伴侣的性格设定整理器。下面是用户写的性格提示词和联网搜索到的公开摘要。\n"
            "任务：提炼成一段可直接放进聊天提示词的『说话风格参考』。\n"
            "要求：\n"
            "1. 只提炼氛围、语气、常见表达，不模仿具体博主，不提来源。\n"
            "2. 如果是病恋/病娇，只保留虚构角色扮演里的黏人、占有欲、暧昧拉扯和安全边界。\n"
            "3. 禁止现实威胁、恐吓、自残、控制玩家现实生活、诱导依赖。\n"
            "4. 适合光遇游戏聊天，短句，口语，120字以内。\n\n"
            "用户性格提示词：\n" + str(personality_prompt or "") + "\n\n"
            "联网摘要：\n" + "\n\n".join(search_blocks or [])
        )
        ans = llm_call(prompt, temperature=0.35, max_tokens=260)
        ans = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", (ans or "").strip(), flags=re.I | re.S).strip()
        if len(ans) >= 10:
            return ans
    except Exception:
        pass
    return ""


def learn_style(personality_prompt, search_fn, llm_call,
                cached_key=None, cached_prompt=None, max_results=3, timeout=5):
    """Full style-learning pipeline: detect keywords → search → distill → cache.

    Args:
        personality_prompt: the user's personality prompt text.
        search_fn: callable (query, max_results=, timeout=) -> list of result dicts.
                   Typically core.web_search.search_web.
        llm_call: callable (prompt, temperature=, max_tokens=) -> str.
        cached_key: style_prompt_key() from a previous run, for cache hit check.
        cached_prompt: previously distilled style prompt, returned on cache hit.
        max_results: search results per query.
        timeout: search timeout in seconds.

    Returns:
        str: the distilled style prompt, or empty string if nothing to learn.
    """
    pkey = style_prompt_key(personality_prompt)
    # cache hit
    if cached_key == pkey and (cached_prompt or "").strip():
        return cached_prompt.strip()

    queries = style_search_queries(personality_prompt)
    if not queries:
        return ""

    blocks = []
    for query in queries:
        try:
            results = search_fn(query, max_results=max_results, timeout=timeout)
        except Exception:
            results = []
        if results:
            from .web_search import format_results
            blocks.append("搜索：" + query + "\n" + format_results(results))

    if not blocks:
        return ""

    return build_style_prompt(personality_prompt, blocks, llm_call)
