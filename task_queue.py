# -*- coding: utf-8 -*-
"""task_queue.py — 纯逻辑任务调度队列（单活动 + 软互斥 + 波次合并 + 超时重投）

用途：替掉 sky-loop 里"多队列 + 一堆全局标志（busy/_sending_msg/各种冷却）"的散装协调。
本模块**不 import 任何项目模块、不碰真实时间、不启线程**，只做纯粹的状态机，
方便先把逻辑跑通、单测钉死，再决定怎么接进主循环。

一、任务类别
    TaskKind.CHAT     聊天（走波次合并）
    TaskKind.INTERACT 互动（牵手/抱抱/姿势/回遇境…按键动作）
    TaskKind.SENSE    识屏（截图/OCR/模板刷新）

二、核心不变量（任何公开方法返回后都成立）
    1. 同一时刻最多一个 active 任务；
    2. active 为 None  =>  等待队列必为空（没有 active 就没有"在等"的意义）；
    3. active 只由 task_done / task_failed / 超时逻辑释放，释放后立刻按 FIFO 放行下一个。

三、软互斥
    跨类才算冲突：新任务与当前 active 不同类时，按投递时给的策略处理：
        MutexPolicy.QUEUE（默认）  排队等（聊天则先凑波次）
        MutexPolicy.DROP          直接丢弃（投递返回 None，stats.dropped +1）
    同类不算冲突（聊天的波次合并就建立在"同类可以进同一个任务"上），
    所以"同类 + DROP"仍然是排队；空闲时也没有冲突，DROP 照样立即执行。
    丢任务不是静默的：返回值 + 统计都看得到。

四、波次合并（仅 CHAT）
    - 空闲时第一条消息**立即**成为 active（零延迟），波次窗口保持打开；
    - active 是"窗口还开着的聊天任务"时，新消息直接并进这个任务的 content
      （同类，不受 DROP 策略影响）；
    - 窗口关闭条件：距最后一次并入满 merge_pause（默认 0.3s）没来新消息，
      或累计达到 merge_max（默认 4 条）——后者是立即关闭；
    - 有**别的类**在跑时，聊天消息先暂存（staging），凑满 merge_max 立即成任务，
      否则等 merge_pause 的"停顿"到了再成任务排队；这一档才受 DROP 策略管。
    合并只发生在窗口内；窗口一关，后面的消息另起一个任务。
    注意：并进 active 意味着 content 会变长，消费方要么在窗口关闭后再读 content，
    要么按"每次读到的就是当前全集"来处理。

五、超时与重投（两条独立的保险）
    - 停顿重投：距上次进展信号（heartbeat/advance）满 stall_timeout（默认 10s）没动静
      -> 若 retries < max_retries（默认 1）就"重投"：同一个任务 id、retries+1、
         计时器重置、插回等待队列最前并立刻重新 active（总预算 first_started_at 不变）；
      -> 重投额度用完还没进展 -> 直接判失败（它不可能自己好了，不必等到 30s）。
    - 硬兜底：距**首次**开始满 hard_timeout（默认 30s）-> 无论有没有心跳都判失败。
    判定顺序与边界：先推进时间再处理调用，比较一律用 >=，所以"刚好卡在 10s/30s"
    这一拍是**超时赢**——心跳要留提前量，别踩着截止线打。

六、时间可注入
    构造时传 time_func（默认 time.time）。模块内没有任何 time.sleep，
    单测用假时钟可以把 30s 的超时瞬间推完。

改动约定：本文件必须保持"纯标准库 + 无副作用"，方便离线单测和后续接入。
"""

import time as _time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

__all__ = [
    "TaskKind", "MutexPolicy", "TaskState", "Task",
    "Stats", "QueueStatus", "TaskQueue",
]


# ===================== 枚举 =====================

class TaskKind(str, Enum):
    """任务类别。继承 str，方便直接和 "chat" 比较、也能进 JSON。"""
    CHAT = "chat"
    INTERACT = "interact"
    SENSE = "sense"

    @classmethod
    def coerce(cls, value: "TaskKind | str") -> "TaskKind":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError(
                "未知任务类别 %r，可选：%s" % (value, [k.value for k in cls])) from None


class MutexPolicy(str, Enum):
    """软互斥策略：跨类冲突时排队还是丢弃。"""
    QUEUE = "queue"
    DROP = "drop"

    @classmethod
    def coerce(cls, value: "MutexPolicy | str") -> "MutexPolicy":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError(
                "未知互斥策略 %r，可选：%s" % (value, [p.value for p in cls])) from None


class TaskState(str, Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


# ===================== 任务 =====================

@dataclass
class Task:
    """一个任务。

    content 的形态：CHAT 一定是 list[str]（波次合并的产物）；其它类别原样保留投递对象。
    时间字段全部来自注入的 time_func。
    """
    id: int
    kind: TaskKind
    content: Any
    created_at: float                 # 入队时间（chat 取波次里第一条消息的时间）
    state: TaskState = TaskState.WAITING
    started_at: float | None = None   # 本次尝试的开始时间（重投会刷新）
    first_started_at: float | None = None  # 首次开始时间，硬兜底按它算，重投不刷新
    last_progress_at: float = 0.0     # 最近一次进展信号
    retries: int = 0                  # 已重投次数
    wave_open: bool = False           # 仅 chat：波次窗口是否还开着（还能被并入）
    wave_last_at: float = 0.0         # 仅 chat：最近一次并入的时间

    @property
    def messages(self) -> list:
        """chat 的消息列表；其它类别返回空列表。"""
        return list(self.content) if self.kind is TaskKind.CHAT else []

    @property
    def text(self) -> str:
        """chat 把多条消息拼成一段；其它类别 str(content)。"""
        if self.kind is TaskKind.CHAT:
            return "\n".join(str(m) for m in self.content)
        return str(self.content)

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind.value, "state": self.state.value,
                "content": self.content, "created_at": self.created_at,
                "started_at": self.started_at,
                "first_started_at": self.first_started_at,
                "last_progress_at": self.last_progress_at,
                "retries": self.retries, "wave_open": self.wave_open}


# ===================== 统计 / 状态快照 =====================

@dataclass(frozen=True)
class Stats:
    completed: int = 0   # 正常完成
    failed: int = 0      # 超时兜底 / 主动失败
    dropped: int = 0     # 因 DROP 策略被丢弃
    retried: int = 0     # 触发过重投的次数
    ignored: int = 0     # 非法的完成/失败调用（无 active、id 不匹配）


@dataclass(frozen=True)
class QueueStatus:
    now: float
    active: Task | None
    active_kind: TaskKind | None
    waiting: int
    waiting_by_kind: dict
    staged: int          # 正在凑波次、还没成为任务的消息数
    busy: dict           # 每个类别"是否正在执行"
    stats: Stats

    def describe(self) -> str:
        a = "无" if self.active is None else "#%d(%s)" % (self.active.id, self.active.kind.value)
        busy = ",".join(k.value for k in TaskKind if self.busy.get(k))
        return ("active=%s 等待=%d 暂存=%d busy=[%s] 完成=%d 失败=%d 丢弃=%d 重投=%d"
                % (a, self.waiting, self.staged, busy,
                   self.stats.completed, self.stats.failed,
                   self.stats.dropped, self.stats.retried))


# ===================== 队列 =====================

class TaskQueue:
    """单活动任务队列。所有方法都不是线程安全的——设计上只由调度方单线程调用。"""

    def __init__(self, time_func: Callable[[], float] | None = None, *,
                 merge_pause: float = 0.3, merge_max: int = 4,
                 stall_timeout: float = 10.0, hard_timeout: float = 30.0,
                 max_retries: int = 1):
        if merge_pause < 0:
            raise ValueError("merge_pause 不能为负")
        if merge_max < 1:
            raise ValueError("merge_max 至少为 1")
        if stall_timeout <= 0 or hard_timeout <= 0:
            raise ValueError("stall_timeout / hard_timeout 必须为正")
        if stall_timeout > hard_timeout:
            raise ValueError("stall_timeout 不能大于 hard_timeout，否则硬兜底永远轮不到")
        if max_retries < 0:
            raise ValueError("max_retries 不能为负")

        self._time = time_func if time_func is not None else _time.time
        self._merge_pause = float(merge_pause)
        self._merge_max = int(merge_max)
        self._stall_timeout = float(stall_timeout)
        self._hard_timeout = float(hard_timeout)
        self._max_retries = int(max_retries)

        self._active: Task | None = None
        self._waiting: deque = deque()
        self._staging: list = []        # [(ts, msg)]
        self._next_id = 1
        self._completed = 0
        self._failed = 0
        self._dropped = 0
        self._retried = 0
        self._ignored = 0

    # ---------- 内部：时间 ----------

    def _now(self) -> float:
        return float(self._time())

    # ---------- 内部：任务构造与放行 ----------

    def _make_task(self, kind, content, created_at, now) -> Task:
        task = Task(id=self._next_id, kind=kind, content=content,
                    created_at=created_at)
        self._next_id += 1
        if kind is TaskKind.CHAT:
            task.wave_open = len(content) < self._merge_max
            task.wave_last_at = now
        return task

    def _admit(self, task: Task, now: float) -> Task:
        """入队：空闲就直接当 active，否则排到等待队列尾部。"""
        if self._active is None:
            self._activate(task, now)
        else:
            task.state = TaskState.WAITING
            self._waiting.append(task)
        return task

    def _activate(self, task: Task, now: float) -> None:
        task.state = TaskState.ACTIVE
        task.started_at = now
        task.last_progress_at = now
        if task.first_started_at is None:
            task.first_started_at = now
        # 重新上场的聊天任务给一个新鲜的合并窗口
        if task.kind is TaskKind.CHAT and task.wave_open:
            task.wave_last_at = now
        self._active = task

    def _promote(self, now: float) -> Task | None:
        if self._active is None and self._waiting:
            self._activate(self._waiting.popleft(), now)
        return self._active

    # ---------- 内部：波次 ----------

    def _flush_staging(self, now: float) -> Task | None:
        """把暂存的聊天消息结算成一个任务。"""
        if not self._staging:
            return None
        items, self._staging = self._staging, []
        created_at = items[0][0]
        task = self._make_task(TaskKind.CHAT, [m for _, m in items], created_at, now)
        return self._admit(task, now)

    # ---------- 内部：超时 ----------

    def _retry_active(self, now: float) -> None:
        """重投：同一个任务 id，插回队首并立刻重新上场（总预算 first_started_at 不变）。"""
        task = self._active
        task.retries += 1
        self._retried += 1
        task.state = TaskState.WAITING
        task.started_at = None
        self._active = None
        self._waiting.appendleft(task)
        self._promote(now)

    def _finish_active(self, now: float, failed: bool) -> None:
        task = self._active
        task.state = TaskState.FAILED if failed else TaskState.DONE
        if failed:
            self._failed += 1
        else:
            self._completed += 1
        self._active = None
        self._promote(now)

    def _check_active_timeouts(self, now: float) -> None:
        task = self._active
        if task is None:
            return
        # 硬兜底优先：到点就认输，不管有没有心跳
        if task.first_started_at is not None \
                and now - task.first_started_at >= self._hard_timeout:
            self._finish_active(now, failed=True)
            return
        # 停顿：要么重投，要么（额度用完）直接失败——它不会自己好了
        if now - task.last_progress_at >= self._stall_timeout:
            if task.retries < self._max_retries:
                self._retry_active(now)
            else:
                self._finish_active(now, failed=True)

    def _advance(self, now: float) -> None:
        """按当前时间推进所有时间驱动的逻辑：关窗口 -> 结算暂存 -> 判超时。"""
        task = self._active
        if task is not None and task.kind is TaskKind.CHAT and task.wave_open \
                and now - task.wave_last_at >= self._merge_pause:
            task.wave_open = False
        if self._staging and now - self._staging[-1][0] >= self._merge_pause:
            self._flush_staging(now)
        self._check_active_timeouts(now)

    # ---------- 投递 ----------

    def enqueue(self, kind: "TaskKind | str", content: Any = None,
                policy: "MutexPolicy | str" = MutexPolicy.QUEUE) -> Task | None:
        """投递一个任务。

        返回：被接受且**已经成形**的任务（可能直接 active，也可能进等待队列）。
              返回 None 有两种含义，用 stats 区分：
                * 被 DROP 策略真正丢弃 -> stats.dropped +1；
                * 聊天消息进了暂存凑波次、还没成形 -> status().staged +1，
                  停顿到点或凑满 merge_max 后它会作为任务出现（非 CHAT 类别不会这样）。
        """
        now = self._now()
        self._advance(now)                      # 先让状态跟上时间，再判断互斥
        k = TaskKind.coerce(kind)
        p = MutexPolicy.coerce(policy)

        if k is TaskKind.CHAT:
            return self._enqueue_chat(now, content, p)

        # 非聊天：只有"和当前 active 不同类"才算冲突
        if self._active is not None and p is MutexPolicy.DROP \
                and self._active.kind is not k:
            self._dropped += 1
            return None
        task = self._make_task(k, content, now, now)
        return self._admit(task, now)

    def _enqueue_chat(self, now: float, content: Any,
                      policy: MutexPolicy) -> Task | None:
        msg = "" if content is None else str(content)
        task = self._active
        # 1) 并进"窗口还开着的聊天任务"（同类不冲突，策略不参与）
        if task is not None and task.kind is TaskKind.CHAT and task.wave_open:
            task.content.append(msg)
            task.wave_last_at = now
            if len(task.content) >= self._merge_max:
                task.wave_open = False
            return task
        # 2) 空闲：立即成为 active，零延迟（窗口开着，后续消息还能并进来）
        if task is None:
            fresh = self._make_task(TaskKind.CHAT, [msg], now, now)
            return self._admit(fresh, now)
        # 3) 有别的类在跑 = 跨类冲突：按策略丢弃
        if task.kind is not TaskKind.CHAT and policy is MutexPolicy.DROP:
            self._dropped += 1
            return None
        # 4) 排队：跨类 QUEUE，或同类但波次窗口已关
        self._staging.append((now, msg))
        if len(self._staging) >= self._merge_max:
            return self._flush_staging(now)
        return None

    # ---------- 消费 ----------

    def current(self) -> Task | None:
        """当前 active 任务（进入时会先推进时间）。"""
        self._advance(self._now())
        return self._active

    def heartbeat(self) -> bool:
        """报告进展，重置停顿计时。返回是否还持有 active。

        注意：超时判定在本次调用之前先跑，所以踩着 10s/30s 截止线心跳会被判定为超时。
        """
        now = self._now()
        self._advance(now)
        if self._active is None:
            return False
        self._active.last_progress_at = now
        return True

    # 同一件事的另一个叫法，方便调用方自选语义
    advance = heartbeat

    def task_done(self, task_id: int | None = None) -> Task | None:
        """完成当前 active，返回被放行的下一个 active（没有则 None）。"""
        return self._finish(task_id, failed=False)

    def task_failed(self, task_id: int | None = None) -> Task | None:
        """主动判失败，返回被放行的下一个 active（没有则 None）。"""
        return self._finish(task_id, failed=True)

    def _finish(self, task_id: int | None, failed: bool) -> Task | None:
        now = self._now()
        self._advance(now)
        task = self._active
        if task is None:
            self._ignored += 1          # 空队列完成：安全空操作
            return None
        if task_id is not None and int(task_id) != task.id:
            self._ignored += 1          # 过期/张冠李戴的完成：不动当前任务
            return None
        self._finish_active(now, failed=failed)
        return self._active

    # ---------- 推进 / 查询 ----------

    def tick(self) -> None:
        """推进一次时间驱动逻辑。所有公开方法内部都会自动调它，单独调也行。"""
        self._advance(self._now())

    def is_busy(self, kind: "TaskKind | str") -> bool:
        """该类别是否正在执行（= 当前 active 的类别）。"""
        self._advance(self._now())
        k = TaskKind.coerce(kind)
        return self._active is not None and self._active.kind is k

    def waiting_count(self, kind: "TaskKind | str | None" = None) -> int:
        """等待队列长度；给 kind 就只数这一类。"""
        self._advance(self._now())
        if kind is None:
            return len(self._waiting)
        k = TaskKind.coerce(kind)
        return sum(1 for t in self._waiting if t.kind is k)

    def status(self) -> QueueStatus:
        """当前状态快照（进入时会先推进时间）。"""
        now = self._now()
        self._advance(now)
        by_kind = {k: 0 for k in TaskKind}
        for t in self._waiting:
            by_kind[t.kind] += 1
        busy = {k: (self._active is not None and self._active.kind is k)
                for k in TaskKind}
        return QueueStatus(
            now=now, active=self._active,
            active_kind=self._active.kind if self._active is not None else None,
            waiting=len(self._waiting), waiting_by_kind=by_kind,
            staged=len(self._staging), busy=busy,
            stats=Stats(completed=self._completed, failed=self._failed,
                        dropped=self._dropped, retried=self._retried,
                        ignored=self._ignored))

    def __repr__(self) -> str:
        return "<TaskQueue %s>" % self.status().describe()
