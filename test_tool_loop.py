# -*- coding: utf-8 -*-
"""离线测试：chat_raw 两条路径 + ai_loop 的视觉工具循环 + ACT 标签解析。

全部 mock，不打真实 API、不连游戏：
  - LLM 用 FakeLLM（按脚本依次返回消息，记录每次收到的 messages/tools）
  - 视觉用 FakeVision（返回预设文本或抛异常）
  - ai_loop 本身是**真代码**（跑在临时线程里，用假 det/state/队列驱动），不是复制品
  - add_turn / _maybe_update_memory 被替换掉，绝不写 user_data/memory.json

覆盖（对应 2026-10-05 二十/二十一章的改动）：
  1. chat_raw：deepseek 直连 HTTP 与 OpenAI SDK 两条路径都能拿到 tool_calls，且 tools 被转发
  2. 工具循环：模型直接回复 / 调一次工具后回复 / 连续调用到轮数上限 / 工具抛异常 /
     视觉不可用不传 tools / 未知工具 / arguments 坏 JSON / 场景问题换标准 prompt
  3. ACT 标签解析：带空格/换行变体（本次修复的回遇境漏判）

运行：py test_tool_loop.py
"""
import contextlib
import importlib.util
import io
import json
import os
import queue
import sys
import threading
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("skyloop_tooltest", "sky-loop-v7.py")
LC = sys.modules["core.llm_client"]      # chat_raw 所在模块（sky-loop 已 import 过）

FRAME = object()                          # 假帧：FakeVision 不真的看图


def expect(desc, cond, extra=""):
    assert cond, "%s -> 失败%s" % (desc, ("；" + extra) if extra else "")
    print("  [PASS] " + desc)


def tool_call(name="look_at_screen", args=None, tid="call_1"):
    if args is None:
        args = {"question": "我们前面有什么"}
    return {"id": tid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


# ===================== 1. chat_raw：两条路径都要能拿到 tool_calls =====================

print("\n[1] chat_raw（deepseek 直连 HTTP / OpenAI SDK 两条路径）")


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class FakeRequests:
    """替掉 core.llm_client 里的 requests 模块（只在本文件生效）。"""
    RequestException = Exception

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "payload": json, "timeout": timeout})
        return self.resp


HTTP_TOOL_MSG = {"role": "assistant", "content": None,
                 "tool_calls": [tool_call()]}

_real_requests = LC.requests
try:
    fake_req = FakeRequests(FakeResp(200, {"choices": [{"message": HTTP_TOOL_MSG}],
                                           "usage": {"prompt_tokens": 10, "completion_tokens": 5}}))
    LC.requests = fake_req
    client = M.LLMClient(provider="deepseek", api_key="sk-test",
                         base_url="https://api.deepseek.com", model="deepseek-flash")
    msg = client.chat_raw([{"role": "user", "content": "看看我"}], tools=[{"type": "function"}])
    expect("HTTP 路径：tool_calls 原样返回",
           msg.get("tool_calls") == HTTP_TOOL_MSG["tool_calls"],
           "实际 %r" % (msg.get("tool_calls"),))
    expect("HTTP 路径：content 为 null 时不报错", msg.get("content") is None)
    expect("HTTP 路径：tools 被转发进请求体",
           fake_req.calls[0]["payload"].get("tools") == [{"type": "function"}])
    expect("HTTP 路径：thinking 仍未开启（flash 必须显式关）",
           fake_req.calls[0]["payload"].get("thinking") == {"type": "disabled"})
    expect("HTTP 路径：last_raw_message 已记录（供 ai_trace）",
           client.last_raw_message is not None)
    expect("HTTP 路径：不带 tools 时请求体里没有 tools 键",
           (client.chat_raw([{"role": "user", "content": "x"}]) is not None
            and "tools" not in fake_req.calls[1]["payload"]))

    LC.requests = FakeRequests(FakeResp(200, {"choices": []}))
    expect("HTTP 路径：choices 为空 -> 返回空 assistant 消息、不抛异常",
           client.chat_raw([{"role": "user", "content": "x"}])
           == {"role": "assistant", "content": ""})

    LC.requests = FakeRequests(FakeResp(400, {"error": "bad request"}))
    try:
        client.chat_raw([{"role": "user", "content": "x"}])
        expect("HTTP 路径：4xx 抛 RuntimeError", False)
    except RuntimeError as e:
        expect("HTTP 路径：4xx 抛 RuntimeError", "HTTP 400" in str(e))
finally:
    LC.requests = _real_requests


class FakeSDKMessage:
    def __init__(self, d):
        self._d = d

    def model_dump(self):
        return dict(self._d)


def make_fake_sdk(script):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        d = script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=FakeSDKMessage(d))])

    return SimpleNamespace(calls=calls,
                           chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


sdk_msg = {"role": "assistant", "content": None, "tool_calls": [tool_call(tid="call_sdk")]}
fake_sdk = make_fake_sdk([sdk_msg])
sdk_client = M.LLMClient(provider="openrouter", api_key="", base_url="https://openrouter.ai/api/v1",
                         model="anthropic/claude-sonnet-4.5")
sdk_client._sdk_client = fake_sdk
got = sdk_client.chat_raw([{"role": "user", "content": "看看我"}], tools=[{"type": "function"}])
expect("SDK 路径：model_dump 里的 tool_calls 能取到",
       got.get("tool_calls") == sdk_msg["tool_calls"])
expect("SDK 路径：tools 被转发给 create()",
       fake_sdk.calls[0].get("tools") == [{"type": "function"}])

fake_sdk2 = make_fake_sdk([{"role": "assistant", "content": "[CHAT]你好[/CHAT]"}])
sdk_client._sdk_client = fake_sdk2
expect("SDK 路径：chat() 只返回 content 字符串",
       sdk_client.chat([{"role": "user", "content": "x"}]) == "[CHAT]你好[/CHAT]")
expect("SDK 路径：不传 tools 时 create() 里没有 tools 键",
       "tools" not in fake_sdk2.calls[0])


# ===================== 2. ai_loop 的工具循环（跑真代码） =====================

print("\n[2] ai_loop 工具循环（真代码 + 假 LLM/视觉）")


class FakeLLM:
    """按脚本依次返回；记录每次收到的 messages 与 tools。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.tools_seen = []

    def chat_raw(self, messages, temperature=0.7, max_tokens=300, tools=None):
        self.calls.append([dict(m) for m in messages])
        self.tools_seen.append(tools)
        if not self.script:
            return {"role": "assistant", "content": "[CHAT]（脚本用完）[/CHAT]"}
        return self.script.pop(0)

    def chat(self, messages, temperature=0.7, max_tokens=300):
        return ""


class FakeVision:
    def __init__(self, answers=None, available=True, raises=None):
        self.available = available
        self.answers = list(answers or [])
        self.asked = []
        self.raises = raises

    def ask(self, frame, question, max_tokens=300, temperature=0.1, crop=None):
        self.asked.append(question)
        if self.raises:
            raise self.raises
        return self.answers.pop(0) if self.answers else "画面：前面有个浮岛"


def make_state():
    return SimpleNamespace(
        lock=threading.Lock(), conversation=[], memory_updating=False,
        memory={"version": 2, "profile_prompt": "", "profile_updated_at": "",
                "raw_turns": [], "pending_turns": []},
        my_words=[], search_cache={}, search_knowledge=[],
        style_checked=True, style_knowledge={},          # True：跳过会联网的风格学习
        is_holding_hands=False, pose_state="standing",
        pose_need_retry=False, pose_last_target="", gohome_completed=False)


def run_ai_loop(script, msgs_list, vision, expect_items=1, extra_wait=0.4):
    """跑真 ai_loop（临时线程），返回 (action_q 收到的东西, 捕获的输出, FakeLLM)。"""
    M.shutdown_event.clear()
    saved = (M._vision, M.add_turn, M._maybe_update_memory)
    M._vision = vision
    M.add_turn = lambda memory, p, c: memory                  # 绝不写 user_data/memory.json
    M._maybe_update_memory = lambda state, llm, force=False: None
    out = io.StringIO()
    llm = FakeLLM(script)
    try:
        state = make_state()
        ai_q, action_q = queue.Queue(maxsize=4), queue.Queue(maxsize=8)
        det = SimpleNamespace(latest_frame=lambda: FRAME)
        t = threading.Thread(target=M.ai_loop, args=(det, state, ai_q, action_q, llm), daemon=True)
        with contextlib.redirect_stdout(out):
            t.start()
            for one in msgs_list:
                ai_q.put((one, True))
            items = []
            for _ in range(expect_items):
                try:
                    items.append(action_q.get(timeout=6.0))
                except queue.Empty:
                    break
            try:                                              # 再等一小会儿，抓多余出队
                items.append(action_q.get(timeout=extra_wait))
            except queue.Empty:
                pass
            M.shutdown_event.set()
            t.join(timeout=3.0)
        return items, out.getvalue(), llm
    finally:
        M.shutdown_event.clear()
        M._vision, M.add_turn, M._maybe_update_memory = saved


# 2a. 模型直接回复：不该调用工具
v = FakeVision()
items, log, llm = run_ai_loop([{"role": "assistant", "content": "[CHAT]你好呀[/CHAT]"}],
                              [["[珂珂]你好呀"]], v)
expect("模型直接回复：只调 1 次 LLM", len(llm.calls) == 1, "实际 %d" % len(llm.calls))
expect("模型直接回复：tools 已注册（视觉可用时）", llm.tools_seen[0] is not None)
expect("模型直接回复：没有看画面", v.asked == [])
expect("模型直接回复：回复进 action_q",
       items == [("execute", "[CHAT]你好呀[/CHAT]")], "实际 %r" % (items,))
_sys0 = llm.calls[0][0]["content"]
expect("知识库：操作约定常驻注入", "操作约定" in _sys0)
expect("知识库：'你好呀'不相关，不注入游戏常识", "游戏常识" not in _sys0)

# 2a-kb. 消息命中知识库（姿势）
items_k, log_k, llm_k = run_ai_loop(
    [{"role": "assistant", "content": "[CHAT]好[/CHAT]"}],
    [["[珂珂]我们坐下吧"]], FakeVision())
_sys_k = llm_k.calls[0][0]["content"]
expect("知识库：'我们坐下吧'命中姿势常识",
       "游戏常识" in _sys_k and "数字3循环" in _sys_k)
expect("知识库：命中时操作约定仍常驻", "操作约定" in _sys_k)

# 2b. 调一次工具后回复
v = FakeVision(answers=["画面右下角是星河，正坐着"])
items, log, llm = run_ai_loop(
    [{"role": "assistant", "content": None, "tool_calls": [tool_call(tid="call_1")]},
     {"role": "assistant", "content": "[CHAT]我在坐着呢[/CHAT]"}],
    [["[珂珂]你在干嘛"]], v)
expect("工具一次：共调 2 次 LLM", len(llm.calls) == 2, "实际 %d" % len(llm.calls))
expect("工具一次：视觉被问了 1 次，且用的是模型写的 question",
       v.asked == ["我们前面有什么"], "实际 %r" % (v.asked,))
m2 = llm.calls[1]
asst = [m for m in m2 if m.get("role") == "assistant" and m.get("tool_calls")]
toolmsgs = [m for m in m2 if m.get("role") == "tool"]
expect("工具一次：第 2 次请求带上了 assistant(tool_calls) 消息", len(asst) == 1)
expect("工具一次：tool_call_id 与 tool_calls 里的 id 对应",
       len(toolmsgs) == 1 and toolmsgs[0].get("tool_call_id") == "call_1",
       "实际 %r" % (toolmsgs,))
expect("工具一次：tool 消息的 content 就是视觉返回",
       toolmsgs[0].get("content") == "画面右下角是星河，正坐着")
expect("工具一次：assistant 消息只有 role/content/tool_calls 三个键（不回灌原始字段）",
       set(asst[0].keys()) == {"role", "content", "tool_calls"}, "实际 %r" % (sorted(asst[0]),))
expect("工具一次：最终回复进 action_q",
       items == [("execute", "[CHAT]我在坐着呢[/CHAT]")], "实际 %r" % (items,))

# 2c. 连续调用：初始 + 2 轮 = 3 次调用，必须停住（不无限循环）
three = [{"role": "assistant", "content": None, "tool_calls": [tool_call(tid="c%d" % i)]}
         for i in (1, 2, 3)]
v = FakeVision()
items, log, llm = run_ai_loop(list(three), [["[珂珂]你看看"]], v)
expect("连续调用：最多 3 次 LLM 调用（初始+2 轮），不会无限循环",
       len(llm.calls) == 3, "实际 %d" % len(llm.calls))
expect("连续调用：只执行了前 2 轮的工具（第 3 次的 tool_calls 未执行）",
       len(v.asked) == 2, "实际 %d" % len(v.asked))
expect("连续调用：轮数用尽且 content 为空 -> 出队空回复（记录该行为）",
       items == [("execute", "")], "实际 %r" % (items,))

# 2c2. 轮数用尽但这次带了 content：应当用 content 回复
last_with_text = {"role": "assistant", "content": "看过了，前面是浮岛",
                  "tool_calls": [tool_call(tid="c3")]}
v = FakeVision()
items, log, llm = run_ai_loop(three[:2] + [last_with_text], [["[珂珂]你看看"]], v)
expect("连续调用：轮数用尽但模型给了 content -> 用它回复",
       items == [("execute", "[CHAT]看过了，前面是浮岛[/CHAT]")], "实际 %r" % (items,))

# 2d. 工具抛异常：不崩线程，但这一轮静默丢失（记录爆炸半径）
v = FakeVision(raises=RuntimeError("vision boom"))
items, log, llm = run_ai_loop(
    [{"role": "assistant", "content": None, "tool_calls": [tool_call()]},
     {"role": "assistant", "content": "[CHAT]第二次正常回[/CHAT]"}],
    [["[珂珂]第一句"], ["[珂珂]第二句"]], v, expect_items=1)
expect("工具异常：异常被 ai_loop 兜住并打印", "[AI] 异常" in log and "vision boom" in log)
expect("工具异常：线程没死，下一条消息仍能正常回复",
       items == [("execute", "[CHAT]第二次正常回[/CHAT]")], "实际 %r" % (items,))

# 2e. 视觉不可用：不注册工具
v = FakeVision(available=False)
items, log, llm = run_ai_loop([{"role": "assistant", "content": "[CHAT]好[/CHAT]"}],
                              [["[珂珂]在吗"]], v)
expect("视觉不可用：tools 传 None（模型无从调用）", llm.tools_seen[0] is None)
expect("视觉不可用：正常出回复", items == [("execute", "[CHAT]好[/CHAT]")])

# 2f. 未知工具名
v = FakeVision()
items, log, llm = run_ai_loop(
    [{"role": "assistant", "content": None, "tool_calls": [tool_call(name="other_tool")]},
     {"role": "assistant", "content": "[CHAT]好的[/CHAT]"}],
    [["[珂珂]试试"]], v)
tm = [m for m in llm.calls[1] if m.get("role") == "tool"]
expect("未知工具：回一条占位 tool 消息（保持 tool_call_id 配对，避免 API 报错）",
       len(tm) == 1 and tm[0]["content"] == "（未知工具）" and v.asked == [])

# 2g. arguments 坏 JSON / 缺字段 -> 用兜底问题
for bad_args, desc in [("{不是json", "坏 JSON"), ({}, "缺 question"), ({"question": ""}, "空 question")]:
    v = FakeVision()
    run_ai_loop([{"role": "assistant", "content": None,
                  "tool_calls": [tool_call(args=bad_args)]},
                 {"role": "assistant", "content": "[CHAT]嗯[/CHAT]"}],
                [["[珂珂]看看"]], v)
    expect("arguments %s -> 用兜底问题，不抛异常" % desc,
           v.asked == ["描述一下当前画面"], "实际 %r" % (v.asked,))

# 2h. 场景类问题：换成标准开放 prompt（二十一章的修复）
v = FakeVision()
run_ai_loop([{"role": "assistant", "content": None,
              "tool_calls": [tool_call(args={"question": "这里是哪里？遇境还是云野？"})]},
             {"role": "assistant", "content": "[CHAT]哦[/CHAT]"}],
            [["[珂珂]这是哪"]], v)
expect("场景类问题：模型写的二选一问题被换成标准 prompt",
       len(v.asked) == 1 and v.asked[0] != "这里是哪里？遇境还是云野？"
       and "霞谷" in v.asked[0], "实际 %r" % (v.asked,))
v = FakeVision()
run_ai_loop([{"role": "assistant", "content": None,
              "tool_calls": [tool_call(args={"question": "玩家现在是什么姿势"})]},
             {"role": "assistant", "content": "[CHAT]嗯[/CHAT]"}],
            [["[珂珂]我什么姿势"]], v)
expect("非场景问题：原样用模型写的问题", v.asked == ["玩家现在是什么姿势"])


# ===================== 3. ACT 标签解析（任务3 修复：带空格变体） =====================

print("\n[3] ACT 标签解析（回遇境漏判修复）")

A = M.extract_act_names
expect("标准写法", A("[ACT]回遇境[/ACT]") == ["回遇境"])
expect("标签内带空格", A("[ACT] 回遇境 [/ACT]") == ["回遇境"])
expect("标签内带换行", A("[ACT]\n回遇境\n[/ACT]") == ["回遇境"])
expect("带空格变体也能判出回遇境", "回遇境" in A("[ACT] 回遇境 [/ACT]"))
expect("混在 CHAT 里", A("[CHAT]回去啦[/CHAT][ACT] 回遇境 [/ACT]") == ["回遇境"])
expect("多个动作按顺序取出",
       A("[ACT]坐下[/ACT][ACT] 回遇境 [/ACT]") == ["坐下", "回遇境"])
expect("其它动作不误判为回遇境",
       "回遇境" not in A("[ACT]牵手[/ACT]") and A("[ACT] 牵手 [/ACT]") == ["牵手"])
expect("空回复 / None -> 空列表", A("") == [] and A(None) == [])
expect("没有 ACT 标签 -> 空列表", A("[CHAT]只是聊天[/CHAT]") == [])
expect("畸形半截标签不匹配", A("[ACT]回遇境") == [] and A("回遇境[/ACT]") == [])
expect("标签内是空白 -> 产出空串（调用方按名字比对，不会误判）",
       A("[ACT]   [/ACT]") == [""])

print("\n全部通过")
