"""离线单测 task_queue.py 纯逻辑调度队列。

全部用假时钟（FakeClock）驱动，不真实 sleep：30s 的兜底、10s 的重投都是瞬间推完的。
覆盖：基本投递/完成、单活动与 FIFO、软互斥（QUEUE/DROP、跨类 vs 同类）、
      波次合并（立即满 4 / 0.3s 停顿 / 间隔合并 / 并进 active / 窗口关闭后另起）、
      10s 重投、重投上限、30s 硬兜底、心跳保活、边界（空队列完成/重复完成/过期 id）、
      不变量、参数校验、状态查询、注入时钟。

运行：py test_task_queue.py
"""
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("task_queue", os.path.join(HERE, "task_queue.py"))
tq = importlib.util.module_from_spec(spec)
sys.modules["task_queue"] = tq   # dataclass 解析注解时要能在 sys.modules 里找到本模块
spec.loader.exec_module(tq)

CHAT = tq.TaskKind.CHAT
INTERACT = tq.TaskKind.INTERACT
SENSE = tq.TaskKind.SENSE
ACTIVE = tq.TaskState.ACTIVE
DONE = tq.TaskState.DONE
FAILED = tq.TaskState.FAILED


class FakeClock:
    """可控时钟：调用它得到当前时间，advance() 手动推进。"""

    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


def new_queue(start=0.0, **kw):
    clock = FakeClock(start)
    return tq.TaskQueue(time_func=clock, **kw), clock


def expect(desc, cond, extra=""):
    assert cond, "%s -> 失败%s" % (desc, ("；" + extra) if extra else "")
    print("  [PASS] " + desc)


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# ===================== 1. 基本投递 / 完成 =====================

print("\n[1] 基本投递与完成")
q, c = new_queue()
expect("空队列没有 active", q.current() is None and q.status().active is None)

t = q.enqueue("chat", "你好")
expect("空闲时投递 -> 立即成为 active",
       t is not None and q.current() is t and t.state is ACTIVE)
expect("任务字段齐：唯一 id / 类型 / 内容 / 入队时间",
       t.id == 1 and t.kind is CHAT and t.content == ["你好"] and t.created_at == c.t)
expect("开始时间 = 入队时间，首次开始时间已记",
       t.started_at == c.t and t.first_started_at == c.t and t.last_progress_at == c.t)

nxt = q.task_done()
expect("task_done 后释放 active", q.current() is None and nxt is None)
expect("完成统计 +1", q.status().stats.completed == 1)
expect("id 递增：下一个任务是 2", q.enqueue("sense", "截屏").id == 2)

q, c = new_queue()
t = q.enqueue("interact", "抱抱")
expect("心跳返回 True 且刷新进展时间", q.heartbeat() is True and t.last_progress_at == c.t)
expect("advance 是 heartbeat 的别名", q.advance() is True)
q.task_done()
expect("空闲时心跳返回 False", q.heartbeat() is False)


# ===================== 2. 单活动 + 等待队列顺序 =====================

print("\n[2] 单活动 / 队列顺序")
q, c = new_queue()
a = q.enqueue("interact", "A")
b = q.enqueue("sense", "B")
cc = q.enqueue("interact", "C")
d = q.enqueue("sense", "D")
st = q.status()
expect("同一时刻只有一个 active",
       st.active.id == a.id and st.active_kind is INTERACT and st.waiting == 3)
expect("等待中的任务不会被提前激活", b.state is tq.TaskState.WAITING)

q.task_done()
expect("严格 FIFO：放行 B", q.current() is b and q.current().content == "B")
q.task_done()
expect("严格 FIFO：放行 C", q.current() is cc and q.current().content == "C")
q.task_done()
expect("严格 FIFO：放行 D", q.current() is d and q.current().content == "D")
q.task_done()
expect("全部放行完 active 为空、队列清空", q.current() is None and q.waiting_count() == 0)


# ===================== 3. 软互斥 =====================

print("\n[3] 软互斥（跨类冲突 / 同类不冲突）")
q, c = new_queue()
q.enqueue("interact", "牵手")
expect("跨类 + DROP -> 丢弃并返回 None", q.enqueue("sense", "截屏", policy="drop") is None)
expect("丢弃计数 +1", q.status().stats.dropped == 1)
expect("丢弃的任务不进等待队列", q.waiting_count() == 0)
expect("跨类 + 默认 QUEUE -> 排队", q.enqueue("sense", "截屏") is not None and q.waiting_count() == 1)

same = q.enqueue("interact", "回遇境", policy=tq.MutexPolicy.DROP)
expect("同类 + DROP 不算冲突 -> 接受并排队",
       same is not None and same.kind is INTERACT and q.waiting_count() == 2)

q2, c2 = new_queue()
idle = q2.enqueue("sense", "截屏", policy="drop")
expect("空闲 + DROP -> 没有冲突，照常立即 active",
       idle is not None and q2.current() is idle and q2.status().stats.dropped == 0)

q3, c3 = new_queue()
q3.enqueue("chat", "占住")
q3.task_done()
q3.enqueue("interact", "抱抱")
expect("跨类 + DROP：active 是互动时，聊天也会被丢",
       q3.enqueue("chat", "路人话", policy="drop") is None)
expect("被丢的聊天不进暂存，且计入 dropped",
       q3.status().staged == 0 and q3.status().stats.dropped == 1)

q4, c4 = new_queue()
t4 = q4.enqueue("chat", "1")
q4.enqueue("chat", "2"); q4.enqueue("chat", "3"); q4.enqueue("chat", "4")
expect("同类合并到 4 条 -> 窗口关闭", t4.wave_open is False and len(t4.content) == 4)
expect("同类 + 窗口已关 + DROP -> 不算冲突，仍然收下（进暂存）",
       q4.enqueue("chat", "5", policy="drop") is None
       and q4.status().staged == 1 and q4.status().stats.dropped == 0)


# ===================== 4. 波次合并 =====================

print("\n[4] 聊天波次合并")
q, c = new_queue()
t = q.enqueue("chat", "1")
q.enqueue("chat", "2")
q.enqueue("chat", "3")
expect("窗口内多条并进同一个 active 任务",
       t.content == ["1", "2", "3"] and q.current() is t and q.waiting_count() == 0)
q.enqueue("chat", "4")
expect("累计满 4 条 -> 立即关闭窗口", t.wave_open is False and t.content == ["1", "2", "3", "4"])
expect("合并过程只产生一个任务", q.status().active.id == t.id)

q, c = new_queue()
t = q.enqueue("chat", "a")
c.advance(0.2); q.enqueue("chat", "b")
c.advance(0.2); q.enqueue("chat", "c")
expect("每条间隔 0.2s（未满 0.3s 停顿）-> 仍然合并",
       t.content == ["a", "b", "c"] and t.wave_open is True)
c.advance(0.2); q.tick()
expect("累计停顿 0.2s 还不够 -> 窗口仍开", t.wave_open is True)
c.advance(0.5); q.tick()
expect("停顿超过 0.3s -> 窗口关闭", t.wave_open is False)

ts_d = c.t
q.enqueue("chat", "d")
expect("窗口关闭后新消息先暂存（不再并进老任务）",
       q.status().staged == 1 and t.content == ["a", "b", "c"])
c.advance(0.5); q.tick()
expect("暂存停顿到点 -> 结算成第二个聊天任务并排队",
       q.waiting_count("chat") == 1 and q.status().staged == 0)
nxt = q.task_done()
expect("第二个任务内容独立、id 递增", nxt.content == ["d"] and nxt.id == t.id + 1)
expect("入队时间取波次第一条消息的时间（不是结算时刻）", nxt.created_at == ts_d)

# 边界：用二进制可精确表示的 0.25 当窗口，把 >= 语义钉死（避免浮点误差干扰）
q, c = new_queue(merge_pause=0.25)
t = q.enqueue("chat", "a")
c.advance(0.25); q.tick()
expect("停顿刚好等于窗口 -> 关闭（比较用 >=）", t.wave_open is False)

q, c = new_queue(merge_pause=0.25)
t = q.enqueue("chat", "a")
c.advance(0.125); q.tick()
expect("停顿不到窗口 -> 保持打开", t.wave_open is True)

q, c = new_queue()
q.enqueue("chat", "1")
q.enqueue("chat", "2")
q.enqueue("chat", "3")
q.enqueue("chat", "4")
expect("满 4 条立即成任务：不等多条也不会拆成两个",
       q.current().content == ["1", "2", "3", "4"] and q.waiting_count() == 0)

q, c = new_queue()
q.enqueue("interact", "抱抱")          # 占住 active，聊天只能暂存
q.enqueue("chat", "1"); q.enqueue("chat", "2")
q.enqueue("chat", "3"); q.enqueue("chat", "4")
expect("占线时累计满 4 条 -> 立即结算成任务排队",
       q.waiting_count("chat") == 1 and q.status().staged == 0)


# ===================== 5. 10s 重投 =====================

print("\n[5] 10s 无进展 -> 自动重投")
q, c = new_queue()
t = q.enqueue("chat", "在吗")
c.advance(9.5); q.tick()
expect("9.5s 没到门槛 -> 不重投", t.retries == 0 and q.current() is t)
c.advance(0.5); q.tick()                 # 正好 10.0s
expect("满 10.0s 无进展 -> 重投（仍是同一个任务，还在跑）",
       t.retries == 1 and q.current() is t and t.state is ACTIVE)
expect("重投统计 +1", q.status().stats.retried == 1)
expect("重投重置本次计时器：started_at/last_progress 刷新，总预算 first_started_at 不变",
       t.started_at == 10.0 and t.last_progress_at == 10.0 and t.first_started_at == 0.0)
expect("重投不改变任务 id 和内容", t.id == 1 and t.content == ["在吗"])
expect("重投不会误判失败", q.status().stats.failed == 0)

c.advance(9.5); q.tick()
expect("重投后 9.5s 无进展 -> 还没到第二次停顿", t.retries == 1 and q.current() is t)
c.advance(0.5); q.tick()                 # 正好 20.0s
expect("第二次停顿且额度用完 -> 判失败、放行下一个",
       t.state is FAILED and q.current() is None and q.status().stats.failed == 1)
expect("失败不计入完成，重投次数仍为 1",
       q.status().stats.completed == 0 and q.status().stats.retried == 1)

q, c = new_queue(max_retries=0)
t = q.enqueue("sense", "截屏")
c.advance(10.0); q.tick()
expect("max_retries=0 -> 首次停顿直接失败，不重投",
       t.state is FAILED and q.status().stats.retried == 0 and q.status().stats.failed == 1)


# ===================== 6. 30s 硬兜底 =====================

print("\n[6] 30s 硬兜底")
q, c = new_queue()
t = q.enqueue("sense", "截屏")
for _ in range(5):                      # 每 5s 心跳一次，永远不触发 10s 停顿
    c.advance(5.0)
    q.heartbeat()
expect("一直有进展 -> 到 25s 依然是 active、没重投", q.current() is t and t.retries == 0)
c.advance(4.5); q.tick()
expect("29.5s 还没到兜底线", q.current() is t and q.status().stats.failed == 0)
c.advance(0.5); q.tick()                 # 正好 30.0s
expect("满 30.0s -> 硬兜底判失败（有心跳也不行）",
       t.state is FAILED and q.current() is None and q.status().stats.failed == 1)
expect("确认走的是硬兜底而不是停顿重投", q.status().stats.retried == 0)

q, c = new_queue()
t = q.enqueue("sense", "截屏")
c.advance(9.0); q.heartbeat()           # 每 9s 心跳一次，避开 10s 停顿
c.advance(9.0); q.heartbeat()
c.advance(9.0); q.heartbeat()
expect("27s 内持续心跳 -> 不重投不失败", q.current() is t and t.retries == 0
       and q.status().stats.failed == 0)
c.advance(3.0); q.tick()                 # 30.0s
expect("即使一路有心跳，满 30s 仍被兜底", t.state is FAILED
       and q.status().stats.retried == 0 and q.status().stats.failed == 1)

q, c = new_queue()
t = q.enqueue("sense", "截屏")
c.advance(10.0); q.heartbeat()
expect("踩着 10s 截止线心跳 -> 超时判定优先，任务已被重投（心跳没能救回来）",
       t.retries == 1)


# ===================== 7. 边界 =====================

print("\n[7] 边界：空队列 / 重复完成 / 过期 id")
q, c = new_queue()
expect("空队列 task_done 安全返回 None", q.task_done() is None)
expect("空队列 task_failed 安全返回 None", q.task_failed() is None)
expect("无 active 的调用计入 ignored", q.status().stats.ignored == 2)
expect("ignored 不污染完成/失败统计",
       q.status().stats.completed == 0 and q.status().stats.failed == 0)

t = q.enqueue("chat", "x")
q.task_done()
expect("重复完成同一个任务 -> 第二次是空操作，完成数只加 1",
       q.task_done(t.id) is None and q.status().stats.completed == 1)
expect("重复完成计入 ignored", q.status().stats.ignored == 3)

t2 = q.enqueue("sense", "s")
q.task_done(t.id)
expect("用过期的 id 完成 -> 不误伤当前任务",
       q.current() is t2 and t2.state is ACTIVE and q.status().stats.ignored == 4)
q.task_done(t2.id)
expect("id 匹配才真的完成", q.current() is None and q.status().stats.completed == 2)

q, c = new_queue()
a = q.enqueue("interact", "A")
q.enqueue("sense", "B")
q.task_failed(a.id)
expect("主动 task_failed 也放行下一个并计失败",
       q.current().content == "B" and q.status().stats.failed == 1)


# ===================== 8. 不变量 =====================

print("\n[8] 不变量：没有 active 就没有等待者")
q, c = new_queue()
ok = True
for i in range(15):
    if i % 3 == 0:
        q.enqueue("chat", "m%d" % i)
    elif i % 3 == 1:
        q.enqueue("sense", "s%d" % i)
    elif i % 6 == 2:
        q.task_done()
    else:
        q.enqueue("interact", "a%d" % i)
    c.advance(0.4)
    q.tick()
    st = q.status()
    if st.active is None and st.waiting != 0:
        ok = False
expect("任意操作序列后：active 为空 => 等待队列为空", ok)
expect("活动任务始终只有一个", q.status().active is None or isinstance(q.status().active, tq.Task))


# ===================== 9. 注入时钟 =====================

print("\n[9] 注入时钟（不真实等待）")
q, c = new_queue()
real0 = time.time()
t = q.enqueue("sense", "截屏")
c.advance(1_000_000)
q.tick()
expect("时间戳全部来自注入时钟（把时钟推到 100 万秒，任务时间戳仍是注入值）",
       t.created_at == 0.0 and t.first_started_at == 0.0)
expect("推过 30s -> 任务已被兜底判失败", t.state is FAILED)
expect("百万秒推进不产生任何真实等待", time.time() - real0 < 1.0)

q, c = new_queue(start=1000.0)
t = q.enqueue("interact", "抱抱")
expect("时钟起点可任意设置", t.created_at == 1000.0 and t.started_at == 1000.0)


# ===================== 10. 状态查询 =====================

print("\n[10] 状态查询")
q, c = new_queue()
q.enqueue("interact", "A")
q.enqueue("sense", "B")
q.enqueue("sense", "C")
st = q.status()
expect("active / active_kind 正确",
       st.active.content == "A" and st.active_kind is INTERACT)
expect("等待队列长度与分类计数",
       st.waiting == 2 and st.waiting_by_kind[SENSE] == 2 and st.waiting_by_kind[CHAT] == 0)
expect("busy 表：只有正在执行的那类为真",
       st.busy[INTERACT] and not st.busy[SENSE] and not st.busy[CHAT])
expect("is_busy / waiting_count 与 status 一致",
       q.is_busy("interact") and not q.is_busy("sense")
       and q.waiting_count() == 2 and q.waiting_count("sense") == 2 and q.waiting_count("chat") == 0)
expect("统计项齐全", st.stats.completed == 0 and st.stats.failed == 0
       and st.stats.dropped == 0 and st.stats.retried == 0 and st.stats.ignored == 0)
expect("describe() 给出一行摘要", "active=" in st.describe() and "等待=2" in st.describe())
expect("repr() 不炸", "TaskQueue" in repr(q))
q.enqueue("sense", "E")
st2 = q.status()
expect("status 是快照：旧快照不随后续投递变化",
       st.waiting == 2 and st.waiting_by_kind[SENSE] == 2
       and st2.waiting == 3 and st2.waiting_by_kind[SENSE] == 3)


# ===================== 11. 任务对象 =====================

print("\n[11] 任务对象")
q, c = new_queue()
t = q.enqueue("chat", "第一句")
q.enqueue("chat", "第二句")
expect("chat 的 content 一定是 list，text 按行拼接",
       t.messages == ["第一句", "第二句"] and t.text == "第一句\n第二句")

q2, c2 = new_queue()
it = q2.enqueue("interact", "抱抱")
expect("非聊天 content 原样保留",
       it.content == "抱抱" and it.messages == [] and it.text == "抱抱")
obj = {"what": "star"}
sn = q2.enqueue("sense", obj)
expect("任意对象也能当内容（不做字符串化）", sn.content is obj)
d = it.as_dict()
expect("as_dict 字段名稳定",
       d["id"] == it.id and d["kind"] == "interact" and d["state"] == "active"
       and d["content"] == "抱抱" and d["retries"] == 0)


# ===================== 12. 参数校验 =====================

print("\n[12] 参数校验与枚举归一")
q, c = new_queue()
expect("未知任务类别 -> ValueError", raises(ValueError, lambda: q.enqueue("dance", "x")))
expect("未知互斥策略 -> ValueError", raises(ValueError, lambda: q.enqueue("chat", "x", policy="maybe")))
expect("merge_max=0 -> ValueError", raises(ValueError, lambda: new_queue(merge_max=0)))
expect("merge_pause 为负 -> ValueError", raises(ValueError, lambda: new_queue(merge_pause=-0.1)))
expect("stall > hard -> ValueError（否则硬兜底永远轮不到）",
       raises(ValueError, lambda: new_queue(stall_timeout=40, hard_timeout=30)))
expect("超时为 0 -> ValueError", raises(ValueError, lambda: new_queue(hard_timeout=0)))
expect("max_retries 为负 -> ValueError", raises(ValueError, lambda: new_queue(max_retries=-1)))
expect("枚举能归一字符串（大小写不敏感）",
       tq.TaskKind.coerce("CHAT") is CHAT and tq.MutexPolicy.coerce("Drop") is tq.MutexPolicy.DROP)
expect("默认参数符合规格",
       (lambda qq: qq._merge_pause == 0.3 and qq._merge_max == 4
        and qq._stall_timeout == 10.0 and qq._hard_timeout == 30.0
        and qq._max_retries == 1)(new_queue()[0]))


# ===================== 13. 全流程小场景 =====================

print("\n[13] 全流程：互动占线 -> 聊天凑波次 -> 依次执行")
q, c = new_queue()
q.enqueue("interact", "抱抱")
c.advance(0.5)
for m in ["在吗", "看我看我", "抱一下"]:
    q.enqueue("chat", m)
st = q.status()
expect("互动执行期间聊天只暂存，不抢 active",
       st.active_kind is INTERACT and st.waiting == 0 and st.staged == 3)
expect("互动期间 is_busy(interact)=True、聊天不 busy",
       q.is_busy("interact") and not q.is_busy("chat"))
expect("暂存的 3 条还没成为任务（等待队列为空）", q.waiting_count() == 0)

c.advance(0.5); q.tick()
expect("停顿到点 -> 3 条合成 1 个聊天任务排队",
       q.waiting_count("chat") == 1 and q.status().staged == 0)

nxt = q.task_done()
expect("互动完成才轮到聊天，且内容已合并",
       nxt is not None and nxt.kind is CHAT
       and nxt.content == ["在吗", "看我看我", "抱一下"])
expect("聊天任务入队时间 = 第一条消息时间（0.5s，不是结算的 0.8s）",
       nxt.created_at == 0.5)

q.heartbeat()
q.task_done()
final = q.status()
expect("最终统计：完成 2、失败 0、丢弃 0、重投 0",
       final.stats.completed == 2 and final.stats.failed == 0
       and final.stats.dropped == 0 and final.stats.retried == 0)
expect("收尾后队列干净", final.active is None and final.waiting == 0 and final.staged == 0)

print("\n全部通过")
