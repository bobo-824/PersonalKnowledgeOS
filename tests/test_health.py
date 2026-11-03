"""/api/health 的行为与安全约束。"""

from __future__ import annotations

import os

from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from conftest import make_alembic_config

from backend.config import PROJECT_ROOT, get_settings, reset_settings_cache
from backend.db.session import dispose_engine, init_engine
from backend.main import create_app


def test_health_returns_ok_after_migration(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["app_version"]
    assert body["database"]["reachable"] is True
    # 不写死版本号，否则每加一个迁移都要回来改这行。
    # 断言"等于当前 head"同样能抓住"库没升级到最新"这种真问题。
    heads = list(ScriptDirectory.from_config(make_alembic_config()).get_heads())
    assert body["database"]["schema_version"] in heads
    assert body["database"]["error"] is None


def test_health_reports_degraded_but_still_200_when_unmigrated(
    unmigrated_client: TestClient,
) -> None:
    """库还没迁移时不能抛 500。

    始终返回 200、用 status 字段区分，前端自检页才能把原因渲染出来，
    而不是让用户面对一个看不懂的 HTTP 错误。
    """
    response = unmigrated_client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["reachable"] is False
    assert body["database"]["schema_version"] is None
    assert body["database"]["error"], "降级时必须给出错误原因"


def test_health_never_leaks_api_key(
    isolated_data_dir, monkeypatch
) -> None:
    """Blueprint 9.1：API Key 不得出现在日志或接口响应里。"""
    sentinel = "sk-TEST-DO-NOT-LEAK-1234567890"
    monkeypatch.setenv("PKOS_AI_API_KEY", sentinel)
    monkeypatch.setenv("PKOS_AI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("PKOS_AI_MODEL", "example-model")
    reset_settings_cache()

    settings = get_settings()
    assert settings.ai_api_key == sentinel, "夹具没能覆盖 AI 密钥"

    settings.ensure_runtime_dirs()
    init_engine(settings.database_url)
    try:
        with TestClient(create_app(settings)) as test_client:
            response = test_client.get("/api/health")
    finally:
        dispose_engine()
        reset_settings_cache()

    assert response.status_code == 200
    # 只暴露“配没配”，不暴露内容。
    # 判据是 base_url + model（和 factory 同一个），**不看 key**：
    # 本地 Ollama 没有 key 也照样算配好了。
    assert response.json()["ai_configured"] is True
    assert sentinel not in response.text
    assert "example.invalid" not in response.text


def test_health_reports_ai_unconfigured_by_default(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ai_configured"] is False


def test_fixtures_never_touch_real_database(isolated_data_dir) -> None:
    """自检：夹具指向的库必须不是项目真实的 data/app.db。"""
    settings = get_settings()
    real_path = PROJECT_ROOT / "data" / "app.db"

    assert os.environ["PKOS_DATA_DIR"] == str(isolated_data_dir)
    assert settings.database_path != real_path
    assert settings.database_path.parent == isolated_data_dir
    assert settings.database_path.is_relative_to(isolated_data_dir.parent)
