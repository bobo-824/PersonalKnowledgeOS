"""ORM 基类与跨模型共用的列类型、默认值工厂。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """统一时间来源：带时区的 UTC now。"""
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator):
    """SQLite 不保存时区信息，本类型把 aware datetime 归一化成 UTC 后落库，读回时补上 UTC 时区。

    写入 naive datetime 会直接抛错。这道硬约束是为了防住 Blueprint 9.2 的
    “系统时间变化、跨日和时区转换不得导致复习日期错乱”——一旦有代码用了
    datetime.now() 而不是 utcnow()，在写库那一刻就会暴露，而不是等到复习日期算错。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "拒绝写入不带时区的时间。请使用 backend.db.base.utcnow() 生成 UTC 时间。"
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass
