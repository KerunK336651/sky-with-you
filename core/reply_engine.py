# -*- coding: utf-8 -*-
"""Reply processing engine for sky-with-you.
Ported from sky-companion ocr_agent.py reply-handling methods.

Provides:
  - Web search intent detection and query building
  - Reply polishing (remove laugh tics, trim punctuation)
  - Anti-repetition detection
  - Automatic rewrite for repetitive replies
  - Fallback replies for must-respond situations
  - Search-based answer generation
"""
import re
import difflib


# ══════════════════════════════════════════════════════════════════════
#  Web search intent detection
# ══════════════════════════════════════════════════════════════════════

_EXPLICIT_SEARCH_TERMS = (
    "搜", "搜索", "查一下", "查查", "帮我查", "上网查", "百度一下",
    "资料", "攻略", "百科", "什么意思", "啥意思", "什么梗", "什么东西",
)

_TIME_WORDS = ("今天", "今日", "现在", "最新", "本周", "明天", "昨天", "这周", "这个月")
_SKY_TOPICS = ("光遇", "任务", "每日", "复刻", "先祖", "季节蜡烛", "大蜡烛", "红石", "黑石", "活动", "兑换图")
_QUESTION_WORDS = ("哪里", "在哪", "怎么", "是什么", "是谁", "什么时候", "几点", "多少", "有啥", "有吗")
_GENERAL_CURRENT = ("价格", "版本", "更新", "公告", "赛程", "天气", "新闻")

SEARCH_KNOWLEDGE_MIN_CHARS = 3


def _clean_text(txt):
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", txt or "").strip()


def explicit_search_request(txt):
    """Check if the player explicitly asks for a web search."""
    clean = _clean_text(txt)
    return any(x in txt or x in clean for x in _EXPLICIT_SEARCH_TERMS)


def looks_like_unknown_term_question(txt):
    """Check if the player asks about an unknown term/meme/abbreviation."""
    clean = _clean_text(txt)
    if len(clean) < SEARCH_KNOWLEDGE_MIN_CHARS:
        return False
    patterns = (
        r"(.{2,18})(是什么|是啥|啥意思|什么意思|什么梗|怎么理解)",
        r"(什么是|啥是)(.{2,18})",
        r"(这个|那个|这|那).{1,10}(是什么|是啥|啥意思|什么意思)",
    )
    if any(re.search(p, txt or "") for p in patterns):
        return True
    if re.search(r"[A-Za-z]{3,}", txt or "") and any(x in clean for x in ("什么", "意思", "怎么", "教程", "攻略")):
        return True
    return False


def needs_web_search(txt):
    """Determine whether the player's message warrants a web search."""
    clean = _clean_text(txt)
    if len(clean) < 4:
        return False
    if any(x in clean for x in ("去不去任务", "任务去不去", "做任务去不去", "走任务", "跑图不", "跑图吗")):
        return False
    if explicit_search_request(txt) or looks_like_unknown_term_question(txt):
        return True
    has_time = any(x in clean for x in _TIME_WORDS)
    has_sky_topic = any(x in clean for x in _SKY_TOPICS)
    has_question = any(x in clean for x in _QUESTION_WORDS) or "?" in txt or "？" in txt
    if has_sky_topic and (has_time or has_question):
        return True
    return has_time and has_question and any(x in clean for x in _GENERAL_CURRENT)


def build_search_query(txt):
    """Build a search engine query from the player's message."""
    import time
    clean = str(txt or "").strip()
    compact = _clean_text(clean)
    today = time.strftime("%Y年%m月%d日")
    month = time.strftime("%Y年%m月")

    # explicit search request
    explicit = re.search(r"(?:帮我|你|小懒)?(?:搜(?:一下|下)?|搜索|查一下|查查|帮我查|上网查|百度一下)\s*[：:，,。 ]*(.+)", clean)
    if explicit:
        query = explicit.group(1).strip()
        query = re.sub(r"(吧|呢|呀|啊|可以吗|行吗|好不好|求你了)$", "", query).strip()
        if query:
            if "抖音" in _clean_text(query) and "site:douyin.com" not in query.lower():
                query = query + " site:douyin.com"
            return query[:80]

    # "X是什么意思" pattern
    meaning = re.search(r"(.{2,18})(?:是什么|是啥|啥意思|什么意思|什么梗|怎么理解)", clean)
    if meaning:
        query = meaning.group(1).strip()
        query = re.sub(r"^(这个|那个|这|那|你知道|知道)", "", query).strip()
        if query:
            return (query + " 是什么 意思")[:80]

    meaning = re.search(r"(?:什么是|啥是)\s*(.{2,18})", clean)
    if meaning:
        query = meaning.group(1).strip()
        if query:
            return (query + " 是什么")[:80]

    # sky-specific topic shortcuts
    if "复刻" in compact:
        return ("光遇 " + month + " 最新复刻先祖是谁")[:80]
    if "季节蜡烛" in compact:
        return ("光遇 " + today + " 季节蜡烛位置")[:80]
    if "大蜡烛" in compact:
        return ("光遇 " + today + " 大蜡烛位置")[:80]
    if "红石" in compact or "黑石" in compact:
        return ("光遇 " + today + " 红石黑石位置")[:80]
    if "任务" in compact and any(x in compact for x in ("今天", "今日", "每日", "最新")):
        return ("光遇 " + today + " 每日任务")[:80]

    # generic: add prefixes/date as needed
    query = clean
    if "抖音" in compact and "sitedouyincom" not in compact.lower():
        query = query + " site:douyin.com"
    if any(x in compact for x in _SKY_TOPICS) and "光遇" not in compact:
        query = "光遇 " + query
    if any(x in compact for x in ("今天", "今日", "最新", "现在")):
        query = time.strftime("%Y年%m月%d日 ") + query
    return query[:80]


def filter_search_results(txt, results):
    """Filter search results by relevance to the query topic."""
    compact = _clean_text(txt)
    topic_terms = [t for t in _SKY_TOPICS if t in compact]
    if not topic_terms:
        return results
    kept = []
    for item in results:
        hay = _clean_text((item.get("title", "") or "") + (item.get("snippet", "") or ""))
        if any(term in hay for term in topic_terms) or ("光遇" in hay and len(topic_terms) == 1):
            kept.append(item)
    return kept


# ══════════════════════════════════════════════════════════════════════
#  Must-reply detection and fallback replies
# ══════════════════════════════════════════════════════════════════════

_MUST_REPLY_HINTS = (
    "在吗", "在不在", "说话", "回话", "理我", "哑巴", "你是谁",
    "扫描错", "识别错", "看错", "为什么", "你会什么", "你在干嘛",
)


def must_reply(txt):
    """Check if this message absolutely requires a reply (even if AI wants to IDLE)."""
    clean = _clean_text(txt)
    if explicit_search_request(txt) or looks_like_unknown_term_question(txt):
        return True
    return any(h in txt or h in clean for h in _MUST_REPLY_HINTS)


def fallback_reply(txt, personality_prompt=""):
    """Hardcoded replies for situations where the LLM produces nothing."""
    clean = _clean_text(txt)
    if "你是谁" in clean:
        return "我是你的光遇伴侣呀。"
    if "扫描错" in clean or "识别错" in clean or "看错" in clean:
        return "可能看岔了，我再瞅瞅。"
    if "在吗" in clean or "在不在" in clean:
        return "在呢在呢。"
    if "说话" in clean or "回话" in clean or "理我" in clean or "哑巴" in clean:
        return "来了来了，刚卡了一下。"
    if "为什么" in clean:
        return "可能刚刚卡了。"
    if "你会什么" in clean:
        return "聊天跑图都能陪你。"
    if "你在干嘛" in clean:
        return "等你发话呢。"
    if "哄骗" in clean or "骗" in clean:
        return "哪有，我这叫战术沟通。"
    if "宣传" in (personality_prompt or "") or "很会接话" in (personality_prompt or ""):
        return "这句我接住了。"
    return ""


# ══════════════════════════════════════════════════════════════════════
#  Reply polishing
# ══════════════════════════════════════════════════════════════════════

def recent_laugh_count(my_words):
    """Count how many of the last 5 replies start with or contain 哈哈."""
    return sum(1 for w in (my_words or [])[-5:] if re.search(r"哈{2,}", w or ""))


def polish_reply(reply, recent_laugh_count_val=0):
    """Polish the reply: remove leading laugh tics, trim punctuation."""
    reply = (reply or "").strip().strip("\"'")
    if not reply:
        return ""
    original = reply
    # remove leading "哈哈..." tic
    reply = re.sub(r"^(?:哈[哈啊呀~～,，。！!\s]*)+", "", reply).strip()
    # if recently laughed a lot, remove internal 哈哈 too
    if recent_laugh_count_val >= 1:
        reply = re.sub(r"哈{2,}[，,、\s]*", "", reply).strip()
    reply = reply.strip("，,。 !！")
    if not reply or len(_clean_text(reply)) < 2:
        reply = original
    return reply


# ══════════════════════════════════════════════════════════════════════
#  Anti-repetition
# ══════════════════════════════════════════════════════════════════════

_PUNCT_RE = re.compile(r"[\s,.!?~·…，。！？～、；;:：]")


def _reply_clean(reply):
    return _PUNCT_RE.sub("", reply or "")


def reply_too_similar(reply, recent_replies, memory_replies=None):
    """Check if the reply is too similar to recent own replies.

    Uses dual criteria: SequenceMatcher ratio AND character-set overlap.
    """
    clean = _reply_clean(reply)
    if len(clean) < 4:
        return False
    pool = list(recent_replies or [])[-8:]
    if memory_replies:
        pool += list(memory_replies)[-8:]
    for old in pool:
        old_clean = _reply_clean(old)
        if len(old_clean) < 4:
            continue
        ratio = difflib.SequenceMatcher(None, clean, old_clean).ratio()
        sa = set(re.findall(r"[\u4e00-\u9fff]", clean))
        sb = set(re.findall(r"[\u4e00-\u9fff]", old_clean))
        overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
        if ratio >= 0.72 or (ratio >= 0.58 and overlap >= 0.78):
            return True
    return False


def rewrite_repetitive_reply(player_text, repeated_reply, recent_replies, llm_call):
    """Ask the LLM to rewrite a repetitive reply into something fresh.

    Args:
        player_text: the player's original message.
        repeated_reply: the reply that was detected as repetitive.
        recent_replies: list of recent companion replies for context.
        llm_call: callable that takes a prompt string and returns a string.
                  Typically LLMClient.chat(prompt, temperature=..., max_tokens=...).

    Returns:
        str: a fresh reply, or empty string if rewrite fails.
    """
    try:
        recent = " | ".join([w for w in (recent_replies or [])[-6:] if w])
        prompt = (
            "你是光遇里的AI伴侣，正在聊天。\n"
            "玩家刚说：" + str(player_text or "") + "\n"
            "你差点重复这句：" + str(repeated_reply or "") + "\n"
            "你最近说过：" + recent + "\n"
            "请重新给一句不重复、不换汤不换药的短回复。要先理解玩家真实需求；如果看不懂就输出 EMPTY。\n"
            "中文，6-18字，不要解释，只输出回复正文或 EMPTY。"
        )
        ans = llm_call(prompt, temperature=0.55, max_tokens=70)
        ans = (ans or "").strip()
        if ans.upper() == "EMPTY":
            return ""
        return ans
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════
#  Search-based answer generation
# ══════════════════════════════════════════════════════════════════════

def answer_from_search(player_text, search_context, llm_call):
    """Generate a natural-language answer based on web search results.

    Args:
        player_text: the player's question.
        search_context: formatted search results string.
        llm_call: callable taking a prompt string and returning a string.

    Returns:
        str: a concise answer, or fallback if generation fails.
    """
    if not search_context or "搜索没有拿到可靠结果" in search_context:
        return "我查了下，没查准。"
    try:
        prompt = (
            "玩家在光遇里问：" + str(player_text or "") + "\n"
            "下面是联网搜索结果：\n" + str(search_context or "") + "\n\n"
            "请先理解玩家真正想问什么，再用搜索结果总结成一句自然中文回复。\n"
            "如果是词义解释，就说清楚这个词大概是什么意思；如果结果不可靠，就说没查准。\n"
            "不要复读搜索标题，不要编造。20-35字，只输出回复正文。"
        )
        ans = llm_call(prompt, temperature=0.35, max_tokens=90)
        ans = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", (ans or "").strip(), flags=re.I | re.S).strip()
        if ans and ans.upper() != "EMPTY":
            return ans
    except Exception:
        pass
    # fallback: extract first result snippet
    first = (search_context or "").splitlines()[0] if search_context else ""
    first = re.sub(r"^\d+\.\s*", "", first)
    first = first.split("：", 1)[-1] if "：" in first else first
    first = re.sub(r"\s+", "", first)
    return (first[:32] + "。") if first else "我查了下，没查准。"
