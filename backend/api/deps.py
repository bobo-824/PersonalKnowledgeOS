"""接口层共用的依赖。

`Depends(get_session)` 已经住在 `db/session.py` 里，这里只补 Provider 一个。
单独成模块是因为 `api/ai.py` 和 `api/settings.py` 都要用它：
写在任何一个路由文件里，另一个就得反向 import 路由，那是分层的倒刺。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from backend.config import get_settings
from backend.services.ai.factory import build_provider
from backend.services.ai.provider import AiProvider


def get_provider() -> Iterator[AiProvider]:
    """按**当前**配置构造一个 Provider，请求结束就关闭。

    为什么不做成模块级单例：
    - 单例意味着设置页改了 base_url / key 之后必须重启进程才生效；
    - 每次请求新建则写完 `.env` 立刻生效，而且拿到的 model_id
      一定和真正发出去的那个值一致（`ai_messages.model` 是 NOT NULL 列）。
      代价是本地单用户应用用不上连接池——这个交换是划算的。

    `close()` 无条件调用，不写 `hasattr` 猜：它已经写进 `AiProvider` 协议，
    "忘记释放 httpx 连接"会表现为静默的 socket 泄漏，比当场报错难查得多。
    """
    provider = build_provider(get_settings())
    try:
        yield provider
    finally:
        provider.close()


ProviderDep = Annotated[AiProvider, Depends(get_provider)]
