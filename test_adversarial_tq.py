"""对抗性验证：重投后 first_started_at（硬超时总预算）不重置。
场景：t=10 停顿重投一次，之后持续心跳保活，t=30（从首次开始算）应硬超时失败。
"""
import importlib.util

spec = importlib.util.spec_from_file_location("tq", "task_queue.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

clock = [0.0]
q = m.TaskQueue(time_func=lambda: clock[0])
q.enqueue("interact", "x")

# t=10 停顿 -> 重投一次
clock[0] = 10.0
q.tick()
cur = q.current()
assert cur is not None and cur.retries == 1, f"t=10 应重投，实际 {cur}"
assert cur.first_started_at == 0.0, "重投不应改动 first_started_at"

# 持续心跳保活（每 5s，停顿永远不触发）
for t in (15.0, 20.0, 25.0):
    clock[0] = t
    assert q.heartbeat(), f"t={t} 应仍持有 active"

# t=30：从首次开始算满 30s，硬超时（即使一直在心跳）
clock[0] = 30.0
q.tick()
st = q.status()
assert q.current() is None, "硬超时应释放 active"
assert st.stats.retried == 1, f"重投次数应为1，实际 {st.stats.retried}"
assert st.stats.failed == 1, f"失败数应为1，实际 {st.stats.failed}"
print("PASS: 重投后总预算不重置，30s 硬超时仍生效")
print("stats:", st.describe())

# 验证 DSH 自承修复的 bug：跨类冲突时 DROP 对 chat 生效
clock2 = [0.0]
q2 = m.TaskQueue(time_func=lambda: clock2[0])
q2.enqueue("interact", "x")                 # active = interact
r = q2.enqueue("chat", "hi", policy="drop")  # 跨类 DROP
assert r is None, "跨类 DROP 聊天应返回 None"
assert q2.status().stats.dropped == 1, "跨类 DROP 应计 dropped"
# 同类 interact 不冲突，DROP 也不丢
r2 = q2.enqueue("interact", "y", policy="drop")
assert r2 is not None, "同类不算冲突，不应丢弃"
print("PASS: 跨类 DROP 聊天被丢弃、同类不丢弃")

