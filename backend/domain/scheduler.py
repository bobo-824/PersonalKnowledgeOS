"""FSRS 的唯一接线点（阶段 3 第②步）。

这个模块刻意**不 import ORM**：收发全是纯数据（`CardState` / 元组），
和 `domain/versioning.py` 同一纪律——领域层可以被任何一层复用，
测试不建库、不联网（0.01 秒级）。

## 三个决定，都有实测依据

1. **`enable_fuzzing=False`，生产也是**。fsrs 默认给复习间隔加随机扰动
   （避免同批卡片挤在同一天），代价是**同样的历史永远算不出同样的结果**，
   测试无法断言。本地单人应用没有"同批卡片"问题，确定性优先。
2. **`reps` / `lapses` / `elapsed_days` / `scheduled_days` 在这里推算**。
   fsrs 6.3.2 的 `Card` 根本没有这四个字段，`ReviewLog` 里也没有
   调度计算的结果（实测只有 rating 和时间戳）——它们只能由
   `review_sessions` 的历史 + 当前排期推出来。
3. **学习步按天，不按分钟（D-074）**。fsrs 默认的 `(1m, 10m)` 是给
   "背单词卡片当场连刷"设计的；这个产品的一条知识是一段理解，
   刚写完就考、十分钟后又考，既测不出遗忘也没有间隔效应。
   改成入库后第 1 / 3 / 7 天各一次，之后交给 FSRS 自适应。

## Card 的销毁与重建

排期表存的是 fsrs `Card` 的**完整可重建状态**。每次复习：
`CardState` → `Card` → `Scheduler.review_card()` → 新 `Card` → 写回排期行。
`card_id` 不持久化（它只是库内部的毫秒时间戳，重建时随机生成不影响结果）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fsrs import Card, Rating, Scheduler, State

from backend.db.models.review_schedule import (
    RATING_AGAIN,
    RATING_EASY,
    RATING_GOOD,
    RATING_HARD,
    RATINGS,
    SCHEDULE_LEARNING,
    SCHEDULE_RELEARNING,
    SCHEDULE_REVIEW,
    SCHEDULE_STATES,
)

# 界面的大白话 → fsrs 的枚举。词表只有这一份，service 层不自己翻译。
_RATING_TO_FSRS = {
    RATING_AGAIN: Rating.Again,
    RATING_HARD: Rating.Hard,
    RATING_GOOD: Rating.Good,
    RATING_EASY: Rating.Easy,
}

_STATE_TO_FSRS = {
    SCHEDULE_LEARNING: State.Learning,
    SCHEDULE_REVIEW: State.Review,
    SCHEDULE_RELEARNING: State.Relearning,
}
_FSRS_TO_STATE = {value: key for key, value in _STATE_TO_FSRS.items()}

# 一条知识入库后第 1 天第一次复习（D-074）。遗忘曲线在 24 小时内最陡，
# 但"刚写完就考"考的是短时记忆，测不出这条理解有没有留下来。
FIRST_REVIEW_DELAY = timedelta(days=1)

# fsrs 的 good 评级取 learning_steps[step+1] 作为下一段间隔，所以想落在
# 第 3、7 天两个节点上，必须给**三个**步长：+2d 到第 3 天，+4d 到第 7 天，
# 第三次 good 毕业，之后由 FSRS 按记忆强度自适应（实测 27d → 102d → 328d）。
LEARNING_STEPS = (timedelta(days=1), timedelta(days=2), timedelta(days=4))

# 评"忘了"回炉后隔 1 天再来，不是 10 分钟后当场重背。
RELEARNING_STEPS = (timedelta(days=1),)


@dataclass(frozen=True)
class CardState:
    """fsrs `Card` 的可持久化投影（不含 `card_id`，理由见模块注释）。"""

    state: str
    step: int
    stability: float | None
    difficulty: float | None
    due: datetime
    last_review: datetime | None


@dataclass(frozen=True)
class DerivedStats:
    """fsrs 不提供、只能从复习历史推算的四个统计值。"""

    reps: int
    lapses: int
    elapsed_days: int
    scheduled_days: int


class UnknownRatingError(ValueError):
    pass


def build_scheduler() -> Scheduler:
    return Scheduler(
        enable_fuzzing=False,
        learning_steps=LEARNING_STEPS,
        relearning_steps=RELEARNING_STEPS,
    )


def new_card_state(due: datetime) -> CardState:
    """一条知识第一次进复习循环时的初始状态。

    到期时间由调用方给：入库时是 `utcnow() + FIRST_REVIEW_DELAY`（D-074）。
    """
    return CardState(
        state=SCHEDULE_LEARNING,
        step=0,
        stability=None,
        difficulty=None,
        due=due,
        last_review=None,
    )


def card_state_from_row(
    *, state: str, step: int, stability: float | None, difficulty: float | None,
    due: datetime, last_review: datetime | None,
) -> CardState:
    """从排期行的裸字段构造。state 值不对就在这里炸，不让坏数据进 fsrs。"""
    if state not in SCHEDULE_STATES:
        raise ValueError(f"排期状态不是合法词表里的值：{state!r}")
    return CardState(
        state=state,
        step=step,
        stability=stability,
        difficulty=difficulty,
        due=due,
        last_review=last_review,
    )


def review(current: CardState, rating: str, now: datetime) -> CardState:
    """复习一次，返回新状态。**只算不存**——落库是 service 层的事。"""
    if rating not in _RATING_TO_FSRS:
        raise UnknownRatingError(
            f"评级只能是 {' / '.join(RATINGS)} 之一，收到的是 {rating!r}"
        )
    card = Card(
        state=_STATE_TO_FSRS[current.state],
        step=current.step,
        stability=current.stability,
        difficulty=current.difficulty,
        due=current.due,
        last_review=current.last_review,
    )
    new_card, _log = build_scheduler().review_card(
        card, _RATING_TO_FSRS[rating], now
    )
    return CardState(
        state=_FSRS_TO_STATE[new_card.state],
        step=new_card.step or 0,
        stability=new_card.stability,
        difficulty=new_card.difficulty,
        due=new_card.due,
        last_review=new_card.last_review,
    )


def derive_stats(
    history: list[tuple[datetime, str]], current_due: datetime, now: datetime
) -> DerivedStats:
    """从复习历史推算四个统计值。

    `history` 是该知识全部复习记录，按时间升序，元素为 (reviewed_at, rating)。
    `current_due` 是排期表里"下一次该复习的时间"——它本身就是
    上一次复习的调度结果，所以 `scheduled_days = current_due − 上次复习时间`。
    """
    reps = len(history)
    lapses = sum(1 for _, rating in history if rating == RATING_AGAIN)
    if not history:
        return DerivedStats(reps=0, lapses=0, elapsed_days=0, scheduled_days=0)
    last_reviewed_at = history[-1][0]
    elapsed_days = max(0, (now - last_reviewed_at).days)
    scheduled_days = max(0, (current_due - last_reviewed_at).days)
    return DerivedStats(
        reps=reps,
        lapses=lapses,
        elapsed_days=elapsed_days,
        scheduled_days=scheduled_days,
    )
