"""设置接口：AI 配置的读、写、测。

写的是 `.env`（阶段 2 开工前用户选的"方案 A：设置页直接写 .env"），
备份、原子替换、行级保留这些具体规则都在 `services/settings_service.py`，
本层只负责把"写不动"这种情况翻译成 HTTP。

## 为什么这里的 GET 是安全的

Blueprint 9.1 / C6：接口和日志不得出现完整密钥。
`GET /api/settings/ai` 返回的是**掩码后的 hint**（末 4 位）加一个布尔值，
完整密钥从来不出现在响应里，也不出现在任何日志行里。
唯一的写入路径是 `PUT`：写进去之后连读都读不回来——
想换密钥只能重新填一遍。这是刻意的：
"能读回来的密钥"意味着前端某处存着它，那才是真的会泄露。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import ProviderDep
from backend.config import Settings, get_settings
from backend.schemas.ai_api import (
    AiConfigUpdateRequest,
    AiConfigUpdateResult,
    AiConfigView,
    AiConnectionTest,
)
from backend.services.ai.factory import probe_connection
from backend.services.settings_service import DotenvUnreadable, get_ai_config, update_ai_config

router = APIRouter(prefix="/api/settings", tags=["settings"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/ai", response_model=AiConfigView)
def get_ai_settings(settings: SettingsDep) -> AiConfigView:
    """当前的 AI 配置。密钥只给末 4 位（见文件顶部的说明）。

    `configured` 和 `api_key_set` 是**两个独立的布尔值**：
    本地 Ollama 属于"地址和模型名都填了、根本没有 key"的合法状态。
    """
    return get_ai_config(settings)


@router.put("/ai", response_model=AiConfigUpdateResult)
def put_ai_settings(
    payload: AiConfigUpdateRequest, settings: SettingsDep
) -> AiConfigUpdateResult:
    """把改过的项写进 `.env`。**没传的字段保持原样。**

    靠 `exclude_unset` 区分"没填"和"填空"，和 `KnowledgeUpdate` 是同一套语义：
    前端只需提交用户真正改动过的输入框。

    写完不需要重启：`update_ai_config` 会清掉配置缓存，
    而 Provider 是每次请求新建的（见 `services/ai/factory.py`）。
    """
    try:
        return update_ai_config(settings, payload.model_dump(exclude_unset=True))
    except DotenvUnreadable as exc:
        # 不改写、不备份、不假装成功：编码问题只能由人来处理，
        # 但"该打开哪个文件、改成什么编码"要说清楚。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/ai/test", response_model=AiConnectionTest)
def test_ai_connection(provider: ProviderDep) -> AiConnectionTest:
    """发一次最小请求，验证地址 / 模型名 / 密钥这三样对不对。

    只读到第一段就关闭（见 `probe_connection`），所以开销是一次极短的补全。
    **这是用户主动点的按钮，不是后台轮询**——D-042 要求付费调用由用户确认。

    未配置时 `Depends(get_provider)` 直接抛 `AiNotConfigured`，
    由 `main.py` 映射成 409，并附上"先去填哪两项"。
    """
    result = probe_connection(provider)
    return AiConnectionTest(
        ok=result.ok,
        model=result.model,
        duration_ms=result.duration_ms,
        error_kind=result.error_kind,
        message=result.message,
    )
