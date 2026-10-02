# -*- coding: utf-8 -*-
"""Style learning for sky-with-you.
Ported from sky-companion ocr_agent.py style-learning methods.

When the personality prompt contains keywords like 病恋/病娇/虚恋/抖音/参考风格,
this module searches the web for public style references and asks the LLM
to distill a speaking-style prompt (with safety filters built in).
"""
import hashlib
import re


def _clean_text(txt):
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", txt or "").strip()


def style_prompt_key(personality_prompt):
    """SHA256 hash (first 24 hex chars) of the personality prompt.

    Used as a cache key so we don't re-learn the same style repeatedly.
    """
    raw = (personality_prompt or "").strip()
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


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
