"""复习接口的请求/响应模型（阶段 3 第④步的 HTTP 契约）。

纪律与 `schemas/ai_api.py` 相同：请求体一律 `extra="forbid"`
（前端拼错字段名必须当场 422），响应模型从 ORM / service 数据类直接构造。

`Literal` 词表与 `db/models/review_schedule.py` 的常量各写一份，
是 D-003 的既有约定：换来的 /docs 里能看到完整枚举，
测试再锁两边不漂移（见 test_review_api.py 的词表锁定测试）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 大白话四级（D-061）。界面文案在前端词表里，这里的值是数据库存储值。
ReviewRating = Literal["again", "hard", "good", "easy"]
ReviewMode = Literal["self_test", "ai_chat"]
ScheduleState = Literal["learning", "review", "relearning"]
ScheduleStatus = Literal["active", "suspended"]

_LIST_LIMIT_MAX = 200
_AI_COMMENT_MAX = 2000


class _ApiModel(BaseModel):
    """请求模型：多传字段一律拒绝。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _ReadModel(BaseModel):
    """响应模型：从 ORM 对象直接构造。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ================================================================ 到期列表


class DueItemRead(_ReadModel):
    """到期列表的一行。**刻意不带理解正文**——列表阶段先把答案
    摆出来，这次复习就白做了（review_service.due_list 的同一决定）。"""

    knowledge_id: str
    title: str
    domain: str | None
    due: datetime
    state: ScheduleState
    reps: int


class DueListRead(_ApiModel):
    items: list[DueItemRead]
    count: int
    generated_at: datetime


class DueCountRead(_ApiModel):
    """首页要的"今天还剩几条"。单独一个端点：首页不需要列表内容。"""

    count: int
    generated_at: datetime


# ================================================================ 排期与评级


class ScheduleRead(_ReadModel):
    """一条知识的排期。详情页的暂停开关和"下次复习"都从这里读。"""

    knowledge_id: str
    state: ScheduleState
    step: int
    stability: float | None
    difficulty: float | None
    due: datetime
    last_review: datetime | None
    status: ScheduleStatus


class ReviewRatingRequest(_ApiModel):
    """提交一次复习。`rating` 是**用户自己打的分**，这是唯一权威（C7）。

    AI 对话式复习时，前端把 AI 的建议放进 `suggested_rating` 存档——
    它只进历史记录，服务端排期计算不读它（有测试锁定）。
    `conversation_id` 指向问答原文（mode=ai_chat 时前端带上）；
    `duration_ms` 由前端从出题展示计时到提交，量不到就不传。
    """

    rating: ReviewRating
    mode: ReviewMode
    suggested_rating: ReviewRating | None = None
    ai_comment: str | None = Field(default=None, max_length=_AI_COMMENT_MAX)
    conversation_id: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class DerivedStatsRead(_ReadModel):
    """fsrs 不提供、从复习历史推算的四个统计值。"""

    reps: int
    lapses: int
    elapsed_days: int
    scheduled_days: int


class ReviewOutcomeRead(_ApiModel):
    knowledge_id: str
    rating: str
    mode: str
    state: str
    due: datetime
    stats: DerivedStatsRead


class SuspensionRequest(_ApiModel):
    """暂停 / 恢复复习（D-060）。幂等，重复暂停不报错。"""

    suspended: bool


class SuspensionResult(_ApiModel):
    knowledge_id: str
    status: ScheduleStatus


# ================================================================ 历史


class ReviewChatStartRead(_ApiModel):
    """开一场对话式复习的响应：会话 id + AI 的第一道题。

    问答原文在 ai_conversations / ai_messages（kind="review"）里，
    后续追问复用学习室那套 SSE 消息端点，前端拿 conversation_id 发即可。
    """

    conversation_id: str
    title: str
    first_question: str


class RatingSuggestionRequest(_ApiModel):
    """要评级建议时指明是哪场复习对话。"""

    conversation_id: str


class ReviewSessionRead(_ReadModel):
    """一条复习历史。`suggested_rating` 非 None 表示那次是 AI 对话式复习；
    mode=ai_chat 而 conversation_id 为 None，说明原文已被用户清空。"""

    id: str
    reviewed_at: datetime
    rating: str
    mode: str
    suggested_rating: ReviewRating | None
    ai_comment: str | None
    conversation_id: str | None
    duration_ms: int | None
