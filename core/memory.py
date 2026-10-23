# -*- coding: utf-8 -*-
"""Long-term memory system for sky-with-you.
Ported from sky-companion user_settings.py memory functions.

Manages three knowledge stores:
  - memory.json:          long-term conversation understanding
  - search_knowledge.json: persisted web search summaries
  - style_knowledge.json:  learned speaking style references

2026-10-19 (v3) 改进（学校机）：
  - 三个 json 全部原子写（.tmp + os.replace），覆盖前自动留 .bak；
    读取时主文件损坏自动回退 .bak，不再静默清空记忆。
  - 长期记忆拆成「稳定 profile_stable / 最近 profile_recent」两段，整合器输出
    【稳定】/【最近】标记段；稳定段无新增信息时原样保留，解决"350字限制 vs
    全文重写丢信息"的矛盾。旧 profile_prompt 字段继续写（新旧代码互兼容，可回退）。
  - search_knowledge 支持 TTL（默认14天过期，SKY_SEARCH_KNOWLEDGE_TTL_DAYS 可调，
    <=0 永久）；检索不再"全不相关也硬塞 top1"。
"""
import json
import os
import re
from datetime import date, datetime, timedelta

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "user_data")
MEMORY_FILE = os.path.join(USER_DATA_DIR, "memory.json")
SEARCH_KNOWLEDGE_FILE = os.path.join(USER_DATA_DIR, "search_knowledge.json")
STYLE_KNOWLEDGE_FILE = os.path.join(USER_DATA_DIR, "style_knowledge.json")
DIARY_FILE = os.path.join(USER_DATA_DIR, "diary.json")
WEEKLY_FILE = os.path.join(USER_DATA_DIR, "weekly.json")
MONTHLY_FILE = os.path.join(USER_DATA_DIR, "monthly.json")
YEARLY_FILE = os.path.join(USER_DATA_DIR, "yearly.json")

# ── constants ──────────────────────────────────────────────────────────
MEMORY_VERSION = 3
RAW_TURN_KEEP = 80
PENDING_TURN_KEEP = 30
SEARCH_KNOWLEDGE_KEEP = 40
PROFILE_STABLE_KEEP = 1200
PROFILE_RECENT_KEEP = 600
# 事件日记上限（实测每条 ~178B：4000 条 ≈ 700KB，json 解析+序列化 ≈ 40ms/次，
# 相对 LLM 几百 ms 的调用可忽略；env 可调。`每发一条消息都要整体读+写本文件`
# 是硬约束，所以上限不能无限放大，上万条就该改成分片存储而不是继续调大本值。）
DIARY_KEEP = int(os.environ.get("SKY_DIARY_KEEP", "4000") or 4000)
# 分层总结（记忆金字塔）：事件 → 周记（周一切分）→ 月记 → 年记。
# 每层都是独立小文件，低频写入，不随 diary.json 线性膨胀；env 可调。
WEEKLY_KEEP = int(os.environ.get("SKY_WEEKLY_KEEP", "260") or 260)   # 约 5 年
MONTHLY_KEEP = int(os.environ.get("SKY_MONTHLY_KEEP", "120") or 120)  # 10 年
YEARLY_KEEP = int(os.environ.get("SKY_YEARLY_KEEP", "30") or 30)
WEEKLY_TEXT_KEEP = 600
MONTHLY_TEXT_KEEP = 900
YEARLY_TEXT_KEEP = 1600


def _env_float(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except Exception:
        return default


# 搜索知识过期天数（<=0 表示永久保留）。时效性内容（每日蜡烛位置等）过期后不再注入。
SEARCH_KNOWLEDGE_TTL_DAYS = _env_float("SKY_SEARCH_KNOWLEDGE_TTL_DAYS", 14.0)


# ── 原子读写（防崩溃瞬间写坏 json 导致记忆静默丢失） ──────────────────────

def _atomic_write_json(path, data):
    """原子写 JSON：先写 .tmp 再 os.replace；覆盖前把旧文件挪成 .bak 备份。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        if os.path.exists(path):
            os.replace(path, path + ".bak")
    except Exception:
        pass
    os.replace(tmp, path)


def _load_json(path):
    """读 JSON：主文件不存在/损坏时回退 .bak；两者都失败返回 None。"""
    if not os.path.exists(path):
        # 主文件不存在但 .bak 存在（极端情况：写到一半断电），也尝试恢复
        if os.path.exists(path + ".bak"):
            try:
                with open(path + ".bak", "r", encoding="utf-8-sig") as f:
                    print(f"[Memory] 警告: {os.path.basename(path)} 缺失，从 .bak 恢复")
                    return json.load(f)
            except Exception:
                return None
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Memory] 警告: {os.path.basename(path)} 损坏（{e}），尝试 .bak")
        try:
            with open(path + ".bak", "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════════════
#  Long-term memory
# ══════════════════════════════════════════════════════════════════════

def _empty_memory():
    return {
        "version": MEMORY_VERSION,
        "profile_stable": "",
        "profile_recent": "",
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


def _combined_profile(memory):
    """合并稳定+最近两段，写进 legacy 字段 profile_prompt（让旧版代码也能读，可回退）。"""
    stable = (memory.get("profile_stable") or "").strip()
    recent = (memory.get("profile_recent") or "").strip()
    if stable and recent:
        return stable + "\n\n" + recent
    return stable or recent


def _normalize_memory(data):
    memory = _empty_memory()
    if isinstance(data, list):
        turns = [_normalize_turn(item) for item in data]
        turns = [item for item in turns if item]
        memory["raw_turns"] = turns[-RAW_TURN_KEEP:]
        memory["pending_turns"] = turns[-PENDING_TURN_KEEP:]
        return memory
    if isinstance(data, dict):
        stable = str(data.get("profile_stable", "") or "").strip()
        recent = str(data.get("profile_recent", "") or "").strip()
        legacy = str(data.get("profile_prompt", "") or "").strip()
        # 旧版数据迁移：没有新字段但有 legacy profile 时，先整体当作稳定段，
        # 下次记忆整合会按【稳定】/【最近】重新拆分。
        if not stable and not recent and legacy:
            stable = legacy
        memory["profile_stable"] = stable[:PROFILE_STABLE_KEEP]
        memory["profile_recent"] = recent[:PROFILE_RECENT_KEEP]
        memory["profile_prompt"] = _combined_profile(memory)
        memory["profile_updated_at"] = str(data.get("profile_updated_at", "") or "")
        raw_turns = [_normalize_turn(item) for item in data.get("raw_turns", [])]
        pending_turns = [_normalize_turn(item) for item in data.get("pending_turns", [])]
        memory["raw_turns"] = [item for item in raw_turns if item][-RAW_TURN_KEEP:]
        memory["pending_turns"] = [item for item in pending_turns if item][-PENDING_TURN_KEEP:]
    return memory


def load_memory():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    data = _load_json(MEMORY_FILE)
    return _normalize_memory(data) if data is not None else _empty_memory()


def save_memory(memory):
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    memory = _normalize_memory(memory)
    _atomic_write_json(MEMORY_FILE, memory)
    return memory


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
    stable = memory.get("profile_stable", "").strip()
    recent = memory.get("profile_recent", "").strip()
    legacy = memory.get("profile_prompt", "").strip()
    if stable:
        lines.append(stable)
    if recent:
        lines.append("TA的最近状态（会过期，注意时效，别当成永久事实）：\n" + recent)
    if not stable and not recent and legacy:
        lines.append(legacy)
    if not lines:
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
    return bool(pending) and not (
        memory.get("profile_stable", "").strip()
        or memory.get("profile_recent", "").strip()
    )


def _split_profile_full(text):
    """把整合器输出拆成（稳定, 最近, 日记）三段。

    - 带【稳定】/【最近】/【日记】标记的新格式；
    - 缺哪段哪段为空；完全不带标记的纯文本（兼容旧调用）：整体归入稳定段。
    """
    text = str(text or "").strip()
    m_stable = re.search(r"【稳定】(.*?)(?=【最近】|【日记】|$)", text, re.S)
    m_recent = re.search(r"【最近】(.*?)(?=【日记】|$)", text, re.S)
    m_diary = re.search(r"【日记】(.*?)$", text, re.S)
    if not (m_stable or m_recent or m_diary):
        return text, "", ""
    return (
        m_stable.group(1).strip() if m_stable else "",
        m_recent.group(1).strip() if m_recent else "",
        m_diary.group(1).strip() if m_diary else "",
    )


def _split_profile(text):
    """旧接口兼容：只取（稳定, 最近）两段，忽略日记段。"""
    stable, recent, _ = _split_profile_full(text)
    return stable, recent


def update_profile(memory, profile_prompt):
    """Write the consolidated long-term understanding and clear pending_turns.

    profile_prompt 可以是：
      - 带【稳定】/【最近】/【日记】三段标记的新格式（日记段顺带写入 diary.json）；
      - 带【稳定】/【最近】两段标记（无日记段，只更新记忆）；
      - 不带标记的纯文本（兼容旧调用），整体写入稳定段，最近段保持不变。
    """
    memory = _normalize_memory(memory)
    new_stable, new_recent, diary_text = _split_profile_full(profile_prompt)
    if not new_stable and not new_recent and not diary_text:
        return memory
    if new_stable:
        memory["profile_stable"] = new_stable[:PROFILE_STABLE_KEEP]
    if new_recent:
        memory["profile_recent"] = new_recent[:PROFILE_RECENT_KEEP]
    memory["profile_prompt"] = _combined_profile(memory)
    memory["profile_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    memory["pending_turns"] = []
    save_memory(memory)
    if diary_text:
        add_diary_entry(diary_text)
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
    memory = _normalize_memory(memory)
    pending = memory_pending_turns(memory, limit=16)
    transcript = []
    for item in pending:
        player = item.get("player", "").replace("\n", " / ")
        companion = item.get("companion", "").replace("\n", " / ")
        transcript.append(f"{item.get('time', '')} 玩家：{player}\n{companion_name}：{companion}")
    stable = memory.get("profile_stable", "").strip()
    recent = memory.get("profile_recent", "").strip()
    if stable or recent:
        old = "【稳定】\n" + (stable or "暂无") + "\n\n【最近】\n" + (recent or "暂无")
    else:
        old = str(old_profile or "").strip() or "暂无。"
    return (
        "你是光遇AI伴侣的长期记忆整理器。请把最近对话整理成两段长期记忆加一条事件日记。\n"
        "输出格式必须严格遵守（三段都要有，只输出这三段，不要额外说明）：\n"
        "【稳定】\n"
        "（对使用者的长期稳定理解：称呼、关系氛围、偏好、讨厌点、常见玩法、说话风格。"
        "这份数据会长期保留，不会自动过期。如果没有新增稳定信息，就把已有稳定段原样保留，"
        "不要压缩删减。400字以内）\n"
        "【最近】\n"
        "（TA最近/上次的状态和心事：最近在玩什么、看什么、听什么、最近的安排和情绪。"
        "这一段下次整理会被整体覆盖，写过期了也没关系，大胆写。每条以『最近』或『上次』开头。200字以内）\n"
        "【日记】\n"
        "（这批对话里发生过什么事：1~2句话、60字以内、写给未来的自己回忆用。"
        "格式：事件正文|关键词1|关键词2|关键词3 。关键词是检索用的标签，2~3个，"
        "取这件事最独特、最容易被再次提起的词（比如：宝马实习、犬夜叉、遇境迷路）。"
        "如果这批对话只是闲聊没有值得记的事，就写：无|无）\n\n"
        "要求：\n"
        "1. 稳定段只写长期不变的事实；一切『最近/上次』性质的内容必须放进最近段。\n"
        "2. 删除一次性寒暄、重复句、OCR乱码、系统提示、明显误识别、API/程序/日志相关内容。\n"
        "3. 不要编造，不确定就别写。\n"
        "4. 中文，短句，写给AI自己看。\n\n"
        "已有长期记忆：\n" + old + "\n\n"
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
    # 清理搜索引擎摘要里被截断的 HTML 标签残片（如未闭合的 <a href=... ）
    text = re.sub(r"<[^>]*", " ", text)
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


def _search_item_age_days(item):
    """按 time 字段算条目年龄（天）。解析失败按 0（不过期）处理。"""
    try:
        t = datetime.strptime(str(item.get("time", "")), "%Y-%m-%d %H:%M")
        return max(0.0, (datetime.now() - t).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def load_search_knowledge():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    data = _load_json(SEARCH_KNOWLEDGE_FILE)
    if data is None:
        return []
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
    items = items[-SEARCH_KNOWLEDGE_KEEP:]
    # TTL：过期条目不再加载（时效性内容如"今日蜡烛位置"过期即毒数据）
    if SEARCH_KNOWLEDGE_TTL_DAYS > 0:
        fresh = [it for it in items if _search_item_age_days(it) <= SEARCH_KNOWLEDGE_TTL_DAYS]
        if len(fresh) < len(items):
            print(f"[Memory] 搜索知识过期清理: {len(items) - len(fresh)} 条超{SEARCH_KNOWLEDGE_TTL_DAYS:.0f}天")
        items = fresh
    return items


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
    _atomic_write_json(SEARCH_KNOWLEDGE_FILE, safe)
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
    """Build a prompt section from relevant persisted search knowledge.

    只注入与当前消息相关（字符交集 >= 2）的条目；全都不相关就返回空，
    不再硬塞最旧一条（旧条目往往已过期/跑题，注入反而误导）。
    """
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
        return ""
    lines = []
    for item in selected:
        lines.append("- " + item.get("query", "") + "：" + item.get("summary", "").replace("\n", " / "))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  Event diary (事件日记层：带时间戳的回忆时间线，按需检索)
# ══════════════════════════════════════════════════════════════════════

def _parse_diary_line(line, time=None):
    """解析一行日记 '事件正文|关键词1|关键词2' → dict；'无|无'/空 → None。"""
    parts = [p.strip() for p in str(line or "").strip().split("|") if p.strip()]
    if not parts:
        return None
    event = _safe_public_text(parts[0], 80).replace("\n", " / ").strip()
    if not event or event in ("无", "无。", "略", "没有"):
        return None
    tags = []
    for t in parts[1:]:
        t = _safe_public_text(t, 12).strip()
        if t and t not in ("无", "无。", "略") and t not in tags:
            tags.append(t)
    return {
        "time": str(time or datetime.now().strftime("%Y-%m-%d %H:%M")),
        "event": event,
        "tags": tags[:4],
    }


def load_diary():
    """读取事件日记（自动回退 .bak；兼容无 tags 的旧条目）。"""
    data = _load_json(DIARY_FILE)
    if not isinstance(data, list):
        return []
    items = []
    for it in data:
        if not isinstance(it, dict):
            continue
        parsed = _parse_diary_line(
            str(it.get("event", "")) + "|" + "|".join(it.get("tags") or []),
            time=it.get("time"),
        )
        if parsed:
            items.append(parsed)
    return items[-DIARY_KEEP:]


def save_diary(items):
    """规范校验 + 原子写 + DIARY_KEEP 截断 + 相邻事件去重。"""
    safe = []
    seen_events = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        parsed = _parse_diary_line(
            str(it.get("event", "")) + "|" + "|".join(it.get("tags") or []),
            time=it.get("time"),
        )
        if parsed and parsed["event"] not in seen_events:
            seen_events.add(parsed["event"])
            safe.append(parsed)
    safe = safe[-DIARY_KEEP:]
    _atomic_write_json(DIARY_FILE, safe)
    return safe


def add_diary_entry(text, time=None):
    """追加日记。text 可以是【日记】段的多行输出（逐行解析）或单行 '事件|标签'。
    返回实际新增条数（'无|无' 或全部重复时计 0）。"""
    if not str(text or "").strip():
        return 0
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    items = load_diary()
    before = len(items)
    existing = {i.get("event") for i in items}
    for ln in lines:
        parsed = _parse_diary_line(ln, time=time)
        if parsed and parsed["event"] not in existing:
            existing.add(parsed["event"])
            items.append(parsed)
    if len(items) == before:
        return 0
    save_diary(items)
    return len(items) - before


def diary_recent(limit=3):
    """最近的 limit 条日记（默认注入层用，零检索成本）。"""
    return load_diary()[-max(0, int(limit)):]


# 回忆检索同义词扩展（query 归一化：'第一次见面' 这类提法映射到日记常用标签词）
_DIARY_SYNONYMS = [
    ("第一次见面", "首次 相遇"),
    ("第一次遇到", "首次 相遇"),
    ("第一次认识", "首次 相遇"),
    ("第一次聊", "首次"),
    ("第一次", "首次"),
    ("初见", "首次"),
    ("刚认识", "首次 相遇"),
    ("什么时候认识", "首次 相遇"),
    ("认识你", "相遇"),
    ("多久了", "首次 相遇"),
]


def _expand_recall_query(query):
    """检索前把 query 按同义词表扩展（'第一次见面'→ 补 '首次 相遇'），提升召回。"""
    q = str(query or "")
    extra = []
    for pat, syn in _DIARY_SYNONYMS:
        if pat in q and syn not in q and syn not in extra:
            extra.append(syn)
    return (q + " " + " ".join(extra)).strip() if extra else q


def diary_recall(query, limit=3, min_score=4):
    """检索日记：标签命中(权重5) + 字面字符交集(权重1) 双通道打分。
    query 会先经过同义词扩展（'第一次见面'→'首次 相遇'）。

    min_score=4：真实命中通常 7~9 分（标签+5）；纯字面交集 2~3 分是
    常用字噪声（比如'第一次见面'蹭上'见面'两个字），宁可空手让 AI
    诚实说记不清，也绝不注入弱相关日记让 AI 编造记忆。

    返回 [(score, item)]，按分数降序，过滤低于 min_score 的。
    纯本地运算，不花 LLM token。
    """
    query = _expand_recall_query(query)
    q_chars = set(ch for ch in query if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    ranked = []
    for it in load_diary():
        score = 0
        for tag in it.get("tags", []):
            if tag and tag in query:
                score += 5
        hay = set(
            ch for ch in (it.get("event", "") + "".join(it.get("tags", [])))
            if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
        )
        score += len(q_chars & hay)
        if score >= min_score:
            ranked.append((score, it))
    ranked.sort(key=lambda x: (x[0], x[1].get("time", "")), reverse=True)
    return ranked[:limit]


def build_backfill_prompt(turns, companion_name):
    """把存量 raw_turns（带时间戳）构造成回溯整理 prompt（生成首批日记用）。"""
    lines = []
    for t in turns:
        player = str(t.get("player", "")).replace("\n", " / ")[:120]
        companion = str(t.get("companion", "")).replace("\n", " / ")[:120]
        lines.append(f"{t.get('time', '')} 玩家：{player}\n{companion_name}：{companion}")
    return (
        "你是光遇AI伴侣的记忆整理器。下面是历史对话存档（带时间戳），"
        "请把它整理成几条『事件日记』，每条一行，严格按格式输出：\n"
        "YYYY-MM-DD HH:MM|事件正文（30字以内）|关键词1|关键词2\n"
        "要求：\n"
        "1. 时间取对话里出现的时间（取这段对话的代表时刻）；\n"
        "2. 只记真实发生过的事（聊了什么话题、做了什么决定、发生了什么），"
        "纯寒暄/乱码/系统提示跳过不记；\n"
        "3. 关键词 2~3 个，取最独特、最容易被再次提起的词；\n"
        "4. 只输出日记行，不要任何额外说明。最多 8 条。\n\n"
        "对话存档：\n" + "\n\n".join(lines)
    )


def parse_backfill_output(output):
    """解析回溯整理的输出（多行 '时间|事件|标签'），返回可入库的条目列表。"""
    results = []
    for ln in str(output or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = [p.strip() for p in ln.split("|") if p.strip()]
        if len(parts) < 2:
            continue
        t = parts[0] if re.match(r"\d{4}-\d{2}-\d{2}", parts[0]) else None
        rest = parts[1:] if t else parts
        parsed = _parse_diary_line("|".join(rest), time=t)
        if parsed:
            results.append(parsed)
    return results


# ══════════════════════════════════════════════════════════════════════
#  分层记忆（记忆金字塔）：事件日记 → 周记（周一切分）→ 月记 → 年记
# ══════════════════════════════════════════════════════════════════════
#
# 设计要点：
#   - 周记按 ISO 周切分（周一为起点，week_key 形如 "2026-W41"）；
#   - 月记 / 年记按日历年月；每一层直接从下层数据压缩，不层层拼接，
#     避免 ISO 周边界与月边界不一致；
#   - 只压缩"已结束"的周期（本周/本月/当年不生成）；生成幂等，
#     已存在的 period 跳过；压缩后事件层不删除（细节继续保留用于检索）。

def _parse_dt(s):
    """解析 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'，失败返回 None。"""
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def week_key_for(d):
    """日期对应的 ISO 周键（周一切分），形如 '2026-W41'。"""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_range(key):
    """ISO 周键 → (周一 date, 周日 date)。解析失败返回 None。"""
    m = re.match(r"^(\d{4})-W(\d{1,2})$", str(key or ""))
    if not m:
        return None
    try:
        monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except Exception:
        return None
    return monday, monday + timedelta(days=6)


def month_key_for(d):
    return d.strftime("%Y-%m")


def year_key_for(d):
    return str(d.year)


def _load_periods(path):
    """读取分层总结列表（自动回退 .bak；跳过非法条目）。"""
    data = _load_json(path)
    if not isinstance(data, list):
        return []
    items = []
    for it in data:
        if isinstance(it, dict) and str(it.get("period", "")).strip():
            items.append(it)
    return items


def _normalize_period(it, text_keep):
    period = str(it.get("period", "")).strip()
    text = _safe_public_text(str(it.get("text", "") or ""), text_keep).replace("\n", " / ").strip()
    if not period or not text:
        return None
    tags = []
    for t in it.get("tags") or []:
        t = _safe_public_text(t, 12).strip()
        if t and t not in tags:
            tags.append(t)
    return {
        "period": period,
        "start": str(it.get("start", "")).strip(),
        "end": str(it.get("end", "")).strip(),
        "text": text,
        "tags": tags[:6],
    }


def _save_periods(path, items, keep, text_keep):
    safe = []
    seen = set()
    for it in items or []:
        norm = _normalize_period(it, text_keep)
        if norm and norm["period"] not in seen:
            seen.add(norm["period"])
            safe.append(norm)
    safe.sort(key=lambda x: x["period"])
    safe = safe[-keep:]
    _atomic_write_json(path, safe)
    return safe


def load_weekly():
    return _load_periods(WEEKLY_FILE)


def save_weekly(items):
    return _save_periods(WEEKLY_FILE, items, WEEKLY_KEEP, WEEKLY_TEXT_KEEP)


def load_monthly():
    return _load_periods(MONTHLY_FILE)


def save_monthly(items):
    return _save_periods(MONTHLY_FILE, items, MONTHLY_KEEP, MONTHLY_TEXT_KEEP)


def load_yearly():
    return _load_periods(YEARLY_FILE)


def save_yearly(items):
    return _save_periods(YEARLY_FILE, items, YEARLY_KEEP, YEARLY_TEXT_KEEP)


# ── 压缩 prompt ─────────────────────────────────────────────────────────

def _event_lines(events):
    lines = []
    for e in events:
        lines.append(f"{e.get('time', '')} {e.get('event', '')}")
    return lines


def build_weekly_prompt(events, companion_name):
    """把一周的事件日记压缩成一段周记。"""
    return (
        f"你是光遇AI伴侣「{companion_name}」的记忆整理器。下面是某一周的事件日记"
        "（按时间排列），请把它压缩成一段『周记』，让未来的自己一眼想起这一周。\n"
        "输出格式必须严格遵守（只输出这两段，不要额外说明）：\n"
        "【总结】\n"
        "（200~300字，第一人称，像自己写给自己看：这周和珂珂一起做了什么、"
        "去了哪些地方、TA这周的状态和心事、约定或没完成的事。要有具体细节，"
        "不要空话套话；没有的内容不要编）\n"
        "【标签】\n"
        "（4~6个检索用关键词，用|分隔，取这一周最独特、最容易被再次提起的"
        "人/事/地点/话题）\n\n"
        "本周事件日记：\n" + "\n".join(_event_lines(events))
    )


def build_monthly_prompt(weeklies, events, companion_name):
    """把一个月的周记（及事件补充）压缩成一段月记。"""
    weekly_text = "\n".join(f"{w.get('period', '')}：{w.get('text', '')}" for w in weeklies)
    if not weekly_text:
        weekly_text = "（本月无周记，直接依据事件整理）"
    return (
        f"你是光遇AI伴侣「{companion_name}」的记忆整理器。下面是某一个月的周记"
        "（及事件日记补充），请把它压缩成一段『月记』。\n"
        "输出格式必须严格遵守（只输出这两段，不要额外说明）：\n"
        "【总结】\n"
        "（300~400字，第一人称：这个月和珂珂关系与生活的变化、一起经历的重要"
        "事件、TA的成长与心事、反复出现的主题、未完成的约定。站在月底回望，"
        "比周记更概括，突出变化和脉络，不要逐周罗列）\n"
        "【标签】\n"
        "（4~6个检索用关键词，用|分隔，取本月最有代表性的人和事）\n\n"
        "本月周记：\n" + weekly_text
        + "\n\n本月事件日记（补充细节）：\n" + "\n".join(_event_lines(events))
    )


def build_yearly_prompt(monthlies, companion_name):
    """把一年的月记压缩成一段年记。"""
    monthly_text = "\n".join(f"{m.get('period', '')}：{m.get('text', '')}" for m in monthlies)
    return (
        f"你是光遇AI伴侣「{companion_name}」的记忆整理器。下面是某一年的月记，"
        "请把它压缩成一篇『年记』。\n"
        "输出格式必须严格遵守（只输出这两段，不要额外说明）：\n"
        "【总结】\n"
        "（500~700字，第一人称：这一年和珂珂一起经历的大事、关系的进展、"
        "TA生活与心境的变化、共同成长、印象最深的几个瞬间、年末的状态。"
        "站在年末回望，有时间脉络和情感分量，不要逐月罗列）\n"
        "【标签】\n"
        "（6~8个检索用关键词，用|分隔，取这一年最有分量的人和事）\n\n"
        "本年月记：\n" + monthly_text
    )


def parse_summary_output(output, text_keep):
    """解析压缩输出的【总结】/【标签】两段 → (text, tags)。解析失败返回 (None, None)。"""
    raw = str(output or "")
    text = ""
    m = re.search(r"【总结】\s*(.*?)(?=【标签】|$)", raw, flags=re.S)
    if m:
        text = m.group(1)
    else:
        # 兼容不带标记的纯文本输出
        text = raw
    text = _safe_public_text(text, text_keep).strip()
    tags = []
    m = re.search(r"【标签】\s*(.*)$", raw, flags=re.S)
    if m:
        for t in re.split(r"[|｜\n,，、]", m.group(1)):
            t = _safe_public_text(t, 12).strip()
            if t and t not in tags:
                tags.append(t)
    if not text:
        return None, None
    return text, tags[:8]


# ── due 计算与压缩编排 ───────────────────────────────────────────────────

def _events_by_week(events):
    groups = {}
    for e in events:
        dt = _parse_dt(e.get("time"))
        if dt is None:
            continue
        key = week_key_for(dt)
        groups.setdefault(key, []).append(e)
    return groups


def _events_by_month(events):
    groups = {}
    for e in events:
        dt = _parse_dt(e.get("time"))
        if dt is None:
            continue
        key = month_key_for(dt)
        groups.setdefault(key, []).append(e)
    return groups


def due_periods(events=None, today=None):
    """计算所有已结束但尚未生成总结的周期。
    返回 {'weekly': [key,...], 'monthly': [key,...], 'yearly': [key,...]}。"""
    today = today or date.today()
    events = events if events is not None else load_diary()
    cur_week = week_key_for(today)
    cur_month = month_key_for(today)
    cur_year = year_key_for(today)

    weekly_have = {p["period"] for p in load_weekly()}
    monthly_have = {p["period"] for p in load_monthly()}
    yearly_have = {p["period"] for p in load_yearly()}

    due_week = sorted(
        k for k, ev in _events_by_week(events).items()
        if k < cur_week and k not in weekly_have and ev)
    due_month = sorted(
        k for k, ev in _events_by_month(events).items()
        if k < cur_month and k not in monthly_have and ev)
    # 年记依赖已生成的月记（该年所有月份都已结束）
    due_year = sorted(
        k for k in {p["period"][:4] for p in load_monthly()}
        if k < cur_year and k not in yearly_have)
    return {"weekly": due_week, "monthly": due_month, "yearly": due_year}


def summarize_due_periods(client, companion_name="星河", events=None, today=None):
    """把所有已到期的周期逐层压缩并入库。
    顺序：周记 → 月记 → 年记（年记依赖月记）。
    返回 {'weekly': n, 'monthly': n, 'yearly': n, 'items': [...]}。"""
    today = today or date.today()
    events = events if events is not None else load_diary()
    by_week = _events_by_week(events)
    by_month = _events_by_month(events)
    due = due_periods(events=events, today=today)
    added = {"weekly": 0, "monthly": 0, "yearly": 0, "items": []}

    # 1) 周记
    weeklies = load_weekly()
    for key in due["weekly"]:
        chunk = by_week.get(key, [])
        wr = week_range(key)
        start = wr[0].isoformat() if wr else ""
        end = wr[1].isoformat() if wr else ""
        out = client.chat(build_weekly_prompt(chunk, companion_name),
                          temperature=0.2, max_tokens=1000)
        text, tags = parse_summary_output(out, WEEKLY_TEXT_KEEP)
        if not text:
            print(f"  [Summary] 周记 {key} 解析失败，跳过")
            continue
        weeklies.append({"period": key, "start": start, "end": end,
                         "text": text, "tags": tags})
        added["weekly"] += 1
        added["items"].append(("weekly", key))
        print(f"  [Summary] 周记 {key}（{start}~{end}）已生成")
    if added["weekly"]:
        save_weekly(weeklies)

    # 2) 月记（直接读该月事件 + 该月周记）
    monthlies = load_monthly()
    weeklies = load_weekly()
    for key in due["monthly"]:
        chunk = by_month.get(key, [])
        in_month = [w for w in weeklies if w.get("period", "")[:7] == key
                    or (w.get("start", "")[:7] == key)]
        out = client.chat(build_monthly_prompt(in_month, chunk, companion_name),
                          temperature=0.2, max_tokens=1400)
        text, tags = parse_summary_output(out, MONTHLY_TEXT_KEEP)
        if not text:
            print(f"  [Summary] 月记 {key} 解析失败，跳过")
            continue
        monthlies.append({"period": key, "start": key + "-01",
                          "end": key, "text": text, "tags": tags})
        added["monthly"] += 1
        added["items"].append(("monthly", key))
        print(f"  [Summary] 月记 {key} 已生成")
    if added["monthly"]:
        save_monthly(monthlies)

    # 3) 年记（依赖该年月记）
    due = due_periods(events=events, today=today)
    yearlies = load_yearly()
    monthlies = load_monthly()
    for key in due["yearly"]:
        in_year = [m for m in monthlies if m.get("period", "")[:4] == key]
        if not in_year:
            continue
        out = client.chat(build_yearly_prompt(in_year, companion_name),
                          temperature=0.3, max_tokens=2000)
        text, tags = parse_summary_output(out, YEARLY_TEXT_KEEP)
        if not text:
            print(f"  [Summary] 年记 {key} 解析失败，跳过")
            continue
        yearlies.append({"period": key, "start": key + "-01",
                         "end": key + "-12", "text": text, "tags": tags})
        added["yearly"] += 1
        added["items"].append(("yearly", key))
        print(f"  [Summary] 年记 {key} 已生成")
    if added["yearly"]:
        save_yearly(yearlies)
    return added


# ── 注入与检索 ───────────────────────────────────────────────────────────

def weekly_recent(limit=1):
    return sorted(load_weekly(), key=lambda x: x["period"])[-max(0, int(limit)):]


def monthly_recent(limit=1):
    return sorted(load_monthly(), key=lambda x: x["period"])[-max(0, int(limit)):]


def yearly_recent(limit=1):
    return sorted(load_yearly(), key=lambda x: x["period"])[-max(0, int(limit)):]


_LEVEL_LOADERS = {
    "weekly": load_weekly,
    "monthly": load_monthly,
    "yearly": load_yearly,
}


def summary_recall(query, levels=("weekly", "monthly", "yearly"),
                   limit=4, min_score=5):
    """检索分层总结：标签命中(权重5) + 文本字符交集(权重1) 双通道打分。
    返回 [(score, level, item)]，按分数降序，过滤低于 min_score。
    纯本地运算，不花 LLM token。"""
    q_chars = set(ch for ch in str(query or "")
                  if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    ranked = []
    for level in levels:
        loader = _LEVEL_LOADERS.get(level)
        if not loader:
            continue
        for it in loader():
            score = 0
            for tag in it.get("tags", []):
                if tag and tag in query:
                    score += 5
            hay = set(
                ch for ch in (it.get("text", "") + "".join(it.get("tags", [])))
                if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
            score += len(q_chars & hay)
            if score >= min_score:
                ranked.append((score, level, it))
    ranked.sort(key=lambda x: (x[0], x[2].get("period", "")), reverse=True)
    return ranked[:limit]


# ══════════════════════════════════════════════════════════════════════
#  Style knowledge (learned speaking style references)
# ══════════════════════════════════════════════════════════════════════

def load_style_knowledge():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    data = _load_json(STYLE_KNOWLEDGE_FILE)
    return data if isinstance(data, dict) else {}


def save_style_knowledge(data):
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    safe = {
        "version": 1,
        "prompt_key": str((data or {}).get("prompt_key", ""))[:80],
        "updated_at": str((data or {}).get("updated_at", "") or datetime.now().strftime("%Y-%m-%d %H:%M")),
        "style_prompt": str((data or {}).get("style_prompt", "") or "")[:1200],
    }
    _atomic_write_json(STYLE_KNOWLEDGE_FILE, safe)
    return safe
