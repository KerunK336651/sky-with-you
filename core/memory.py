# -*- coding: utf-8 -*-
"""Long-term memory system for sky-with-you.
Ported from sky-companion user_settings.py memory functions.

Manages three knowledge stores:
  - memory.json:          long-term conversation understanding
  - search_knowledge.json: persisted web search summaries
  - style_knowledge.json:  learned speaking style references
"""
import json
import os
import re
from datetime import datetime

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "user_data")
MEMORY_FILE = os.path.join(USER_DATA_DIR, "memory.json")
SEARCH_KNOWLEDGE_FILE = os.path.join(USER_DATA_DIR, "search_knowledge.json")
STYLE_KNOWLEDGE_FILE = os.path.join(USER_DATA_DIR, "style_knowledge.json")

# ── constants ──────────────────────────────────────────────────────────
MEMORY_VERSION = 2
RAW_TURN_KEEP = 80
PENDING_TURN_KEEP = 30
SEARCH_KNOWLEDGE_KEEP = 40


# ══════════════════════════════════════════════════════════════════════
#  Long-term memory
# ══════════════════════════════════════════════════════════════════════

def _empty_memory():
    return {
        "version": MEMORY_VERSION,
        "profile_prompt": "",
        "profile_updated_at": "",
        "raw_turns": [],
        "pending_turns": [],
    }


def _normalize_turn(item):
    if not isinstance(item, dict):
        return None
    player = str(item.get("player", "") or "").strip()
    companion = str(item.get("companion", "") or "").strip()
    if not player and not companion:
        return None
    return {
        "time": str(item.get("time", "") or datetime.now().strftime("%Y-%m-%d %H:%M")),
        "player": player[:160],
        "companion": companion[:160],
    }


def _normalize_memory(data):
    memory = _empty_memory()
    if isinstance(data, list):
        turns = [_normalize_turn(item) for item in data]
        turns = [item for item in turns if item]
        memory["raw_turns"] = turns[-RAW_TURN_KEEP:]
        memory["pending_turns"] = turns[-PENDING_TURN_KEEP:]
        return memory
    if isinstance(data, dict):
        memory["profile_prompt"] = str(data.get("profile_prompt", "") or "").strip()
        memory["profile_updated_at"] = str(data.get("profile_updated_at", "") or "")
        raw_turns = [_normalize_turn(item) for item in data.get("raw_turns", [])]
        pending_turns = [_normalize_turn(item) for item in data.get("pending_turns", [])]
        memory["raw_turns"] = [item for item in raw_turns if item][-RAW_TURN_KEEP:]
        memory["pending_turns"] = [item for item in pending_turns if item][-PENDING_TURN_KEEP:]
    return memory


def load_memory():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    if not os.path.exists(MEMORY_FILE):
        return _empty_memory()
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8-sig") as f:
            return _normalize_memory(json.load(f))
    except Exception:
        return _empty_memory()


def save_memory(memory):
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(_normalize_memory(memory), f, ensure_ascii=False, indent=2)


def add_turn(memory, player_text, companion_text):
    """Append one conversation turn to both raw_turns and pending_turns."""
    memory = _normalize_memory(memory)
    turn = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "player": str(player_text or "")[:120],
        "companion": str(companion_text or "")[:120],
    }
    memory["raw_turns"].append(turn)
    memory["pending_turns"].append(turn)
    memory["raw_turns"] = memory["raw_turns"][-RAW_TURN_KEEP:]
    memory["pending_turns"] = memory["pending_turns"][-PENDING_TURN_KEEP:]
    save_memory(memory)
    return memory


def memory_prompt(memory, limit=12):
    """Build the memory section for the system prompt."""
    memory = _normalize_memory(memory)
    lines = []
    profile = memory.get("profile_prompt", "").strip()
    if profile:
        lines.append(profile)
    else:
        lines.append("暂无稳定长期理解。先按性格提示词自然聊天，不要假装知道没有确认过的事。")
    pending = memory.get("pending_turns", [])[-min(4, limit):]
    if pending:
        lines.append("\n最近还没整理进长期记忆的片段，仅作当前上下文参考：")
    for item in pending:
        lines.append(f"- {item.get('time', '')} 玩家说：{item.get('player', '')}；伴侣回：{item.get('companion', '')}")
    return "\n".join(lines)


def memory_pending_turns(memory, limit=16):
    memory = _normalize_memory(memory)
    return memory.get("pending_turns", [])[-limit:]


def needs_update(memory, min_pending=6):
    """Check if enough pending turns have accumulated to trigger a memory consolidation."""
    memory = _normalize_memory(memory)
    pending = memory.get("pending_turns", [])
    if len(pending) >= min_pending:
        return True
    return bool(pending) and not memory.get("profile_prompt", "").strip()


def update_profile(memory, profile_prompt):
    """Write the consolidated long-term understanding and clear pending_turns."""
    memory = _normalize_memory(memory)
    profile_prompt = str(profile_prompt or "").strip()
    if profile_prompt:
        memory["profile_prompt"] = profile_prompt[:1800]
        memory["profile_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        memory["pending_turns"] = []
        save_memory(memory)
    return memory


def companion_replies(memory, limit=80):
    """Return recent companion replies for anti-repetition checks."""
    memory = _normalize_memory(memory)
    return [
        item.get("companion", "")
        for item in memory.get("raw_turns", [])[-limit:]
        if item.get("companion", "")
    ]


def build_memory_update_prompt(memory, companion_name, old_profile=""):
    """Build the prompt for LLM to consolidate pending turns into a long-term profile."""
    pending = memory_pending_turns(memory, limit=16)
    transcript = []
    for item in pending:
        player = item.get("player", "").replace("\n", " / ")
        companion = item.get("companion", "").replace("\n", " / ")
        transcript.append(f"{item.get('time', '')} 玩家：{player}\n{companion_name}：{companion}")
    old = str(old_profile or "").strip() or "暂无。"
    return (
        "你是光遇AI伴侣的长期记忆整理器。请把最近对话整合成一段可直接放进聊天提示词的长期理解。\n"
        "目标：让AI越来越了解使用者，而不是死记原句。\n"
        "要求：\n"
        "1. 只保留稳定信息：使用者称呼、关系氛围、偏好、讨厌点、常见玩法、说话风格、当前持续状态。\n"
        "2. 删除一次性寒暄、重复句、OCR乱码、系统提示、明显误识别、API/程序/日志相关内容。\n"
        "3. 不要编造，不确定就别写。\n"
        "4. 写给AI自己看，用第二人称/指令式都可以，中文，短句。\n"
        "5. 最近状态必须写成『最近/上次...』，不要当成永久事实。\n"
        "6. 控制在350字以内，只输出整理后的记忆正文。\n\n"
        "已有长期理解：\n" + old + "\n\n"
        "最近对话素材：\n" + "\n\n".join(transcript)
    )


def clean_memory_summary(txt):
    """Clean LLM output for memory profile: strip code blocks, prefixes, secrets."""
    txt = (txt or "").strip()
    txt = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", txt, flags=re.I | re.S).strip()
    txt = txt.replace("长期记忆：", "").strip()
    lines = []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(secret in line.lower() for secret in ("api key", "apikey", "sk-", "base_url", "http://", "https://")):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# ══════════════════════════════════════════════════════════════════════
#  Search knowledge (persisted web search summaries)
# ══════════════════════════════════════════════════════════════════════

def _safe_public_text(text, limit=600):
    text = str(text or "").strip()
    blocked = ("api key", "apikey", "sk-", "base_url", "http://", "https://")
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(secret in low for secret in blocked):
            continue
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)[:limit]


def load_search_knowledge():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    if not os.path.exists(SEARCH_KNOWLEDGE_FILE):
        return []
    try:
        with open(SEARCH_KNOWLEDGE_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        items = []
        for item in data:
            if not isinstance(item, dict):
                continue
            query = _safe_public_text(item.get("query", ""), 80)
            summary = _safe_public_text(item.get("summary", ""), 420)
            if query and summary:
                items.append({
                    "time": str(item.get("time", "") or ""),
                    "query": query,
                    "summary": summary,
                })
        return items[-SEARCH_KNOWLEDGE_KEEP:]
    except Exception:
        return []


def save_search_knowledge(items):
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    safe = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        query = _safe_public_text(item.get("query", ""), 80)
        summary = _safe_public_text(item.get("summary", ""), 420)
        if query and summary:
            safe.append({
                "time": str(item.get("time", "") or datetime.now().strftime("%Y-%m-%d %H:%M")),
                "query": query,
                "summary": summary,
            })
    safe = safe[-SEARCH_KNOWLEDGE_KEEP:]
    with open(SEARCH_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False, indent=2)
    return safe


def add_search_knowledge(query, summary):
    query = _safe_public_text(query, 80)
    summary = _safe_public_text(summary, 420)
    if not query or not summary or "搜索没有拿到可靠结果" in summary:
        return load_search_knowledge()
    items = load_search_knowledge()
    key = "".join(ch for ch in query.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    kept = []
    for item in items:
        old_key = "".join(ch for ch in item.get("query", "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if old_key != key:
            kept.append(item)
    kept.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "query": query,
        "summary": summary,
    })
    return save_search_knowledge(kept)


def search_knowledge_prompt(items, current_text="", limit=3):
    """Build a prompt section from relevant persisted search knowledge."""
    items = load_search_knowledge() if items is None else list(items or [])
    if not items:
        return ""
    current = str(current_text or "")
    current_chars = set(ch for ch in current if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    ranked = []
    for item in items:
        hay = str(item.get("query", "") + item.get("summary", ""))
        hay_chars = set(ch for ch in hay if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        score = len(current_chars & hay_chars)
        ranked.append((score, item))
    ranked.sort(key=lambda x: x[0], reverse=True)
    selected = [item for score, item in ranked if score >= 2][:limit]
    if not selected:
        selected = [item for _, item in ranked[:1]]
    lines = []
    for item in selected:
        lines.append("- " + item.get("query", "") + "：" + item.get("summary", "").replace("\n", " / "))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  Style knowledge (learned speaking style references)
# ══════════════════════════════════════════════════════════════════════

def load_style_knowledge():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    if not os.path.exists(STYLE_KNOWLEDGE_FILE):
        return {}
    try:
        with open(STYLE_KNOWLEDGE_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_style_knowledge(data):
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    safe = {
        "version": 1,
        "prompt_key": str((data or {}).get("prompt_key", ""))[:80],
        "updated_at": str((data or {}).get("updated_at", "") or datetime.now().strftime("%Y-%m-%d %H:%M")),
        "style_prompt": str((data or {}).get("style_prompt", "") or "")[:1200],
    }
    with open(STYLE_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False, indent=2)
    return safe
