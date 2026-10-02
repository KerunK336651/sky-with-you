# -*- coding: utf-8 -*-
"""
聊天引擎离线测试脚本（不需要游戏窗口、不需要Arduino硬件）

用法:
    python test_chat_engine.py

功能:
    - 模拟光遇聊天消息，直接测试AI回复生成
    - 测试长期记忆系统（自动整理）
    - 测试联网搜索
    - 测试白名单过滤
    - 测试防重复 + 回复打磨
    - 测试风格学习
    - 回复只打印到控制台，不发送到游戏

前置条件:
    - 配置 API Key（环境变量 OPENROUTER_API_KEY 或项目目录 key.txt）
    - 安装依赖（pip install -r requirements.txt）
"""
import os
import sys
import re
import time
import json

# 项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

# ====== 配置（可修改） ======
# LLM 提供商: openrouter 或 deepseek
LLM_PROVIDER = os.environ.get("SKY_LLM_PROVIDER", "openrouter")
# 模型（不设则用默认）
LLM_MODEL = os.environ.get("SKY_LLM_MODEL", "")
# 白名单: 1=只回复白名单玩家, 0=所有人
WHITELIST_ENABLED = os.environ.get("SKY_WHITELIST_ENABLED", "1") != "0"
WHITELIST = [n.strip() for n in os.environ.get("SKY_WHITELIST", "珂珂").split(",") if n.strip()]
# 联网搜索: 1=开启 0=关闭
SEARCH_ENABLED = os.environ.get("SKY_SEARCH_ENABLED", "1") != "0"
# 名字
PLAYER_NAME = "珂珂"      # 大号（玩家）
AI_NAME = "星河"           # 小号（AI操控）
# ============================

# 加载核心模块
from core.llm_client import LLMClient
from core.memory import (
    load_memory, add_turn, memory_prompt, needs_update, update_profile,
    companion_replies, memory_pending_turns, build_memory_update_prompt,
    clean_memory_summary, load_search_knowledge, add_search_knowledge,
    search_knowledge_prompt, load_style_knowledge, save_style_knowledge,
)
from core.web_search import search_web, format_results
from core.reply_engine import (
    needs_web_search, build_search_query, filter_search_results,
    polish_reply, reply_too_similar, rewrite_repetitive_reply,
    recent_laugh_count, fallback_reply, must_reply, answer_from_search,
)
from core.style_learner import learn_style, style_prompt_key


# ====== 加载 API Key ======
def load_api_key():
    k = os.environ.get("OPENROUTER_API_KEY", "")
    if k:
        return k
    p = os.path.join(PROJECT_DIR, "key.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

API_KEY = load_api_key()

# ====== 加载人设 ======
def load_persona():
    p = os.path.join(PROJECT_DIR, "persona.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        # 没有 persona.txt 时用默认人设
        return """你是光遇里的AI伴侣，名字叫星河。
性格温柔体贴，说话简短自然，像真实玩家一样聊天。

回复格式（必须遵守）:
[CHAT]说的话[/CHAT]
[ACT]点火/收火/鞠躬/牵手/回家牵手[/ACT]
[KEY]键名 毫秒[/KEY]
[IDLE]（不回复时用）"""

SYSTEM = load_persona()


# ====== 初始化 LLM 客户端 ======
if LLM_PROVIDER == "deepseek":
    base_url = "https://api.deepseek.com"
    model = LLM_MODEL or "deepseek-v4-pro"
else:
    base_url = "https://openrouter.ai/api/v1"
    model = LLM_MODEL or "anthropic/claude-sonnet-4.5"

llm_client = LLMClient(
    provider=LLM_PROVIDER,
    api_key=API_KEY,
    base_url=base_url,
    model=model,
)


# ====== 运行时状态 ======
conversation = []
memory = load_memory()
search_knowledge = load_search_knowledge()
style_knowledge = load_style_knowledge()
style_checked = False
my_words = []
search_cache = {}
memory_updating = False


# ====== 白名单过滤 ======
_WHITELIST_NAME_RE = re.compile(r'^\[([^\]]*)\]')

def in_whitelist(name):
    if not WHITELIST_ENABLED:
        return True
    if not name:
        return False
    name = name.strip()
    for w in WHITELIST:
        if name == w or name.startswith(w) or w.startswith(name):
            return True
    return False

def filter_by_whitelist(msgs):
    if not WHITELIST_ENABLED:
        return msgs
    filtered = []
    for m in msgs:
        match = _WHITELIST_NAME_RE.match(m)
        if match and in_whitelist(match.group(1)):
            filtered.append(m)
        elif match and match.group(1) == AI_NAME:
            filtered.append(m)  # 保留自己的消息
    return filtered


# ====== 记忆整理 ======
def maybe_update_memory(force=False):
    global memory, memory_updating
    if memory_updating:
        return
    if not force and not needs_update(memory, min_pending=6):
        return
    pending = memory_pending_turns(memory, limit=16)
    if not pending:
        return
    memory_updating = True
    try:
        prompt = build_memory_update_prompt(memory, AI_NAME, memory.get("profile_prompt", ""))
        print(f"\n  [记忆] 正在整理 {len(pending)} 条对话...")
        result = llm_client.chat(prompt, temperature=0.2, max_tokens=520)
        summary = clean_memory_summary(result)
        if len(summary) >= 20:
            memory = update_profile(memory, summary)
            print(f"  [记忆] 整理完成: {summary[:80]}...")
        else:
            print("  [记忆] 整理结果太短，跳过")
    except Exception as e:
        print(f"  [记忆] 整理失败: {e}")
    finally:
        memory_updating = False


# ====== 风格学习 ======
def maybe_learn_style():
    global style_knowledge, style_checked
    if style_checked:
        return
    style_checked = True
    pkey = style_prompt_key(SYSTEM)
    cached_key = style_knowledge.get("prompt_key")
    cached_prompt = style_knowledge.get("style_prompt", "")
    if cached_key == pkey and cached_prompt:
        print(f"  [风格] 使用已缓存的风格参考")
        return cached_prompt
    def style_llm(prompt, temperature=0.35, max_tokens=260):
        return llm_client.chat(prompt, temperature=temperature, max_tokens=max_tokens)
    style = learn_style(SYSTEM, search_web, style_llm, cached_key=pkey, cached_prompt=cached_prompt)
    if style:
        style_knowledge = save_style_knowledge({"prompt_key": pkey, "style_prompt": style})
        print(f"  [风格] 学习完成: {style[:60]}...")
    return style


# ====== 核心：处理一条聊天消息 ======
def process_message(player_msg, sender_name=PLAYER_NAME):
    """处理一条玩家消息，返回AI回复"""
    global conversation, memory, my_words, search_cache

    print(f"\n{'='*50}")
    print(f"  [{sender_name}] {player_msg}")
    print(f"{'='*50}")

    # 1. 白名单检查
    if WHITELIST_ENABLED and not in_whitelist(sender_name):
        print(f"  [白名单] {sender_name} 不在白名单中，忽略")
        return None

    # 2. 联网搜索判断
    search_context = ""
    if SEARCH_ENABLED and needs_web_search(player_msg):
        query = build_search_query(player_msg)
        cache_key = query.lower()
        cached = search_cache.get(cache_key)
        if cached and time.time() - cached[0] < 300:
            search_context = cached[1]
            print(f"  [搜索] 使用缓存: {query[:40]}")
        else:
            print(f"  [搜索] 正在搜索: {query[:60]}")
            results = search_web(query, max_results=3, timeout=5)
            results = filter_search_results(player_msg, results)
            if results:
                search_context = format_results(results)
                search_knowledge_local = add_search_knowledge(query, search_context)
                print(f"  [搜索] 找到 {len(results)} 条结果")
            else:
                search_context = "搜索没有拿到可靠结果。"
                print(f"  [搜索] 未找到可靠结果")
            search_cache[cache_key] = (time.time(), search_context)

    # 3. 风格学习（仅第一次）
    style_context = maybe_learn_style()

    # 4. 已学搜索知识
    learned_search = search_knowledge_prompt(search_knowledge, player_msg)

    # 5. 组装增强 system prompt
    system_with_memory = SYSTEM
    system_with_memory += "\n\n## 长期记忆\n" + memory_prompt(memory)
    if style_context:
        system_with_memory += "\n\n## 说话风格参考\n" + style_context
    if learned_search:
        system_with_memory += "\n\n## 已学到的联网知识\n" + learned_search

    # 6. 组装用户消息
    content = f"[{sender_name}] {player_msg}"
    if search_context and "搜索没有拿到可靠结果" not in search_context:
        content += "\n\n## 联网搜索结果\n" + search_context

    conversation.append({"role": "user", "content": content})
    # 保留最近20条
    if len(conversation) > 20:
        conversation = conversation[-20:]

    conv_snapshot = [{"role": "system", "content": system_with_memory}] + list(conversation)

    # 7. 调用 LLM
    print(f"  [AI] 正在生成回复...")
    try:
        reply = llm_client.chat(conv_snapshot, temperature=0.7, max_tokens=300)
    except Exception as e:
        print(f"  [AI] 调用失败: {e}")
        conversation.pop()
        return None

    conversation.append({"role": "assistant", "content": reply})

    # 8. 解析回复
    chat_match = re.search(r'\[CHAT\](.*?)\[/CHAT\]', reply, re.DOTALL)
    chat_text = chat_match.group(1).strip() if chat_match else ""
    act_match = re.search(r'\[ACT\](.*?)\[/ACT\]', reply, re.DOTALL)
    act_text = act_match.group(1).strip() if act_match else ""

    if "[IDLE]" in reply and not chat_text:
        print(f"  [AI] (idle - 不回复)")
        memory = add_turn(player_msg, "")
        maybe_update_memory()
        return None

    # 9. 回复打磨
    if chat_text:
        laugh_count = recent_laugh_count(my_words)
        chat_text = polish_reply(chat_text, laugh_count)

        # 10. 防重复检测
        if chat_text and reply_too_similar(chat_text, my_words, companion_replies(memory)):
            print(f"  [AI] 检测到重复，正在重写...")
            def rewrite_llm(prompt, temperature=0.55, max_tokens=70):
                return llm_client.chat(prompt, temperature=temperature, max_tokens=max_tokens)
            rewritten = rewrite_repetitive_reply(player_msg, chat_text, my_words, rewrite_llm)
            rewritten = polish_reply(rewritten, laugh_count)
            if rewritten and not reply_too_similar(rewritten, my_words):
                chat_text = rewritten
                print(f"  [AI] 重写成功")
            elif not must_reply(player_msg):
                chat_text = ""
                print(f"  [AI] 重写失败且非必须回复，跳过")

        # 11. 记录AI回复
        if chat_text:
            my_words.append(chat_text)
            if len(my_words) > 8:
                my_words.pop(0)

    # 12. 记入记忆
    memory = add_turn(player_msg, chat_text)

    # 13. 触发记忆整理
    maybe_update_memory()

    # 14. 输出结果
    print(f"\n  >>> 原始回复: {reply[:120]}")
    if chat_text:
        print(f"  >>> 聊天消息: {chat_text}")
    if act_text:
        print(f"  >>> 游戏动作: {act_text}")
    print(f"  >>> 记忆状态: {len(memory['raw_turns'])}条总对话, {len(memory['pending_turns'])}条待整理")

    return chat_text


# ====== 主循环（交互式测试） ======
def main():
    global SEARCH_ENABLED, PLAYER_NAME
    print("=" * 60)
    print("  光遇 AI 伴侣 - 聊天引擎离线测试")
    print("=" * 60)
    print()
    print(f"  LLM:      {LLM_PROVIDER} / {model}")
    print(f"  API Key:  {'已配置' if API_KEY else '未配置!!!'}")
    print(f"  白名单:   {'开启 (' + ','.join(WHITELIST) + ')' if WHITELIST_ENABLED else '关闭'}")
    print(f"  联网搜索: {'开启' if SEARCH_ENABLED else '关闭'}")
    print(f"  玩家名:   {PLAYER_NAME}")
    print(f"  AI名:     {AI_NAME}")
    print(f"  记忆:     {len(memory['raw_turns'])}条历史对话")
    print()

    if not API_KEY:
        print("  [错误] 未配置 API Key！")
        print("  请设置环境变量 OPENROUTER_API_KEY 或在项目目录创建 key.txt")
        print()
        return

    print("  输入消息测试聊天引擎（输入 :quit 退出，:memory 查看记忆，:search 切换搜索）")
    print()

    while True:
        try:
            user_input = input(f"\n[{PLAYER_NAME}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n退出测试")
            break

        if not user_input:
            continue
        if user_input.lower() in (":quit", ":q", "exit"):
            print("\n退出测试")
            break
        if user_input.lower() == ":memory":
            print(f"\n  --- 长期记忆 ---")
            print(f"  {memory_prompt(memory)}")
            continue
        if user_input.lower() == ":search":
            SEARCH_ENABLED = not SEARCH_ENABLED
            print(f"\n  联网搜索: {'开启' if SEARCH_ENABLED else '关闭'}")
            continue
        if user_input.startswith(":sender "):
            PLAYER_NAME = user_input[8:].strip()
            print(f"\n  切换发送者为: {PLAYER_NAME}")
            continue

        process_message(user_input, sender_name=PLAYER_NAME)


if __name__ == "__main__":
    main()
