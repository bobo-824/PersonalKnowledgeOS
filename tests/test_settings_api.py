"""设置接口与 `.env` 写入的验收测试（阶段 2 第⑦步，方案 A）。

⚠️ 本文件的测试**绝不碰用户真实的 `.env`**：
`ai_env` 夹具把 `backend.config.ENV_FILE` 和 pydantic-settings 读的
`model_config["env_file"]` 一起指向 pytest 的临时文件，
并且删掉进程里所有 `PKOS_AI_*` 环境变量——否则 conftest 设的那几个空值
会被算成"遮蔽项"，测出来的就是环境变量的行为而不是文件的行为。

三件必须钉住的事：

1. **完整密钥永不出现在响应、日志或备份路径之外的地方**（Blueprint 9.1 / C6）。
   写进去就读不回来，这是刻意的：能读回来意味着前端某处存着它。
2. **只替换 `PKOS_AI_*` 那几行**，其余内容逐字保留（注释、顺序、别的键）。
3. **改完立刻生效**，不需要重启进程（清配置缓存 + Provider 每请求新建）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import config as config_module
from backend.api.deps import get_provider
from backend.config import ConfigFileError, Settings, get_settings, reset_settings_cache
from backend.db.session import dispose_engine, init_engine
from backend.main import create_app
from backend.services.ai.errors import AiAuthRejected, AiUnavailable
from backend.services.ai.factory import (
    AiNotConfigured,
    build_provider,
    is_configured,
    probe_connection,
)
from backend.services.ai.openai_compat import OpenAICompatProvider
from backend.services.settings_service import (
    BACKUP_KEEP,
    DotenvUnreadable,
    get_ai_config,
    merge_ai_lines,
    render_value,
    update_ai_config,
)

SENTINEL_KEY = "SENTINEL-sk-abcd1234ef56"
BASE_BODY = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}


# ==================================================================== 夹具


@pytest.fixture
def ai_env(isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把 .env 重定向到临时文件，并清掉进程里的 PKOS_AI_* 变量。"""
    path = tmp_path / ".env"
    monkeypatch.setattr(config_module, "ENV_FILE", path)
    monkeypatch.setitem(Settings.model_config, "env_file", path)
    for name in [key for key in os.environ if key.startswith("PKOS_AI_")]:
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    try:
        yield path
    finally:
        reset_settings_cache()


@pytest.fixture
def env_client(ai_env: Path, migrated_database: Path) -> Iterator[TestClient]:
    """跑在临时库 + 临时 .env 上的客户端（用真实的 factory，不覆盖 Provider）。"""
    init_engine(get_settings().database_url)
    with TestClient(create_app(get_settings())) as test_client:
        yield test_client
    dispose_engine()


@pytest.fixture
def probe_env(ai_env: Path, migrated_database: Path) -> Iterator[None]:
    """临时 .env + 已迁移的库 + 活着的引擎，但不建客户端。

    分成两个夹具而不是给 `env_client` 参数化一个 Provider，
    是为了让"哪个测试用了哪个假 Provider"写在测试函数里，一眼看得见。
    """
    init_engine(get_settings().database_url)
    yield
    dispose_engine()


def make_probe_client(provider: object) -> TestClient:
    """覆盖 Provider 的客户端，用来测设置页那个"测试连接"按钮。

    不用 `with`：这几个测试不需要 lifespan（迁移和引擎已由 `probe_env` 建好），
    而把 Provider 塞进夹具会让"哪个 Provider 属于哪个测试"变得要靠上下文猜。
    """
    app = create_app(get_settings())
    app.dependency_overrides[get_provider] = lambda: provider  # type: ignore[arg-type]
    return TestClient(app)


class StubProvider:
    """只满足 probe_connection 用到的那部分：model_id + stream_chat + close。"""

    def __init__(self, chunks: list[str] | None = None, error: Exception | None = None) -> None:
        self._chunks = chunks or []
        self._error = error
        self.consumed = 0
        self.closed = False

    @property
    def model_id(self) -> str:
        return "stub-model"

    def stream_chat(self, messages):
        # finally 而不是循环后一行：被测代码是"取到第一段就关闭"，
        # 那意味着循环永远不会自然走完，用后者来记"关过没有"就永远是 False。
        try:
            if self._error is not None:
                raise self._error
            for index, chunk in enumerate(self._chunks):
                self.consumed = index + 1
                yield chunk
        finally:
            self.closed = True

    def close(self) -> None:
        return None


def current_settings() -> Settings:
    return get_settings()


# ================================================================ factory


def test_build_provider_needs_only_url_and_model(ai_env: Path) -> None:
    """⚠️ key 不是必需项：本地 Ollama 不校验密钥，把它设成必填等于挡死这条路线。"""
    monkeypatched = Settings(ai_base_url="http://127.0.0.1:11434/v1", ai_model="qwen2.5")
    assert is_configured(monkeypatched) is True
    assert build_provider(monkeypatched).model_id == "qwen2.5"


def test_build_provider_reports_missing_env_names(ai_env: Path) -> None:
    """报错要给用户**能照着改的**环境变量名，不是 Python 属性名。"""
    with pytest.raises(AiNotConfigured) as excinfo:
        build_provider(Settings())

    assert excinfo.value.missing == ("PKOS_AI_BASE_URL", "PKOS_AI_MODEL")


def test_build_provider_rejects_unknown_provider_type(ai_env: Path) -> None:
    with pytest.raises(AiNotConfigured) as excinfo:
        build_provider(Settings(ai_base_url="https://x.example.com/v1", ai_model="m", ai_provider="anthropic"))

    assert "openai" in excinfo.value.user_message


def test_provider_dependency_closes_the_provider_after_the_request(ai_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_provider` 必须真的把连接关掉。

    真实 Provider 里面是一个 httpx 连接池，"忘记关"不会报错，
    只会静默攒出一堆没关的 socket——所以这条性质得有测试，
    不能靠"代码里看起来有个 finally"。
    """
    update_ai_config(current_settings(), BASE_BODY)
    closed: list[str] = []
    monkeypatch.setattr(
        OpenAICompatProvider, "close", lambda self: closed.append(self.model_id)
    )

    generator = get_provider()
    provider = next(generator)
    assert isinstance(provider, OpenAICompatProvider)
    assert closed == [], "请求还没结束，不该提前关"

    generator.close()  # FastAPI 在依赖作用域退出时做的正是这件事
    assert closed == ["deepseek-chat"]


def test_provider_repr_does_not_contain_the_key(ai_env: Path) -> None:
    """repr 会出现在 traceback 的局部变量表里，所以它必须是白名单式的（C6）。"""
    provider = build_provider(
        Settings(ai_base_url="https://api.example.com/v1", ai_model="m", ai_api_key=SENTINEL_KEY)
    )
    assert SENTINEL_KEY not in repr(provider)
    assert "key_set=True" in repr(provider)
    provider.close()


def test_probe_connection_reads_one_chunk_and_stops(ai_env: Path) -> None:
    """连通性测试只需要证明"链路通"，读满一整条回答是要花钱的。"""
    provider = StubProvider(chunks=["收", "到", "更多"])

    result = probe_connection(provider)

    assert result.ok is True
    assert result.model == "stub-model"
    assert provider.consumed == 1, "只该取第一段就关闭"
    assert provider.closed is True


def test_probe_connection_reports_the_failure_kind(ai_env: Path) -> None:
    """失败时给 kind + 固定文案，用户才知道下一步该改 key 还是该查网络。"""
    provider = StubProvider(error=AiAuthRejected())

    result = probe_connection(provider)

    assert result.ok is False
    assert result.error_kind == "auth_rejected"
    assert result.message == AiAuthRejected.user_message


def test_probe_connection_fails_when_no_text_comes_back(ai_env: Path) -> None:
    """⚠️ 「测试连接说成功、聊天却回空」的错位来源。

    探针的结论必须建立在**真的拿到文字**之上，而不是「没抛异常」之上：
    端点返回 200 但一个碎片都没有时，旧代码会报 ok=True。
    """
    provider = StubProvider(chunks=[])

    result = probe_connection(provider)

    assert result.ok is False
    assert result.error_kind == "schema_invalid"
    assert "没有返回任何文字" in result.message
    assert provider.closed is True


# ================================================================ 行级替换


def test_merge_replaces_only_the_ai_lines() -> None:
    original = (
        "# 我自己的备忘\n"
        "PKOS_PORT=8000\n"
        "PKOS_AI_BASE_URL=\n"
        "PKOS_AI_MODEL=\n"
        "\n"
        "# PKOS_AI_API_KEY=注释掉的例子\n"
        "PKOS_LOG_LEVEL=INFO\n"
    )
    text, changed = merge_ai_lines(
        original,
        {"PKOS_AI_BASE_URL": '"https://x/v1"', "PKOS_AI_MODEL": '"m1"'},
    )

    assert text == (
        "# 我自己的备忘\n"
        "PKOS_PORT=8000\n"
        'PKOS_AI_BASE_URL="https://x/v1"\n'
        'PKOS_AI_MODEL="m1"\n'
        "\n"
        "# PKOS_AI_API_KEY=注释掉的例子\n"
        "PKOS_LOG_LEVEL=INFO\n"
    )
    assert changed == ("PKOS_AI_BASE_URL", "PKOS_AI_MODEL")


def test_merge_appends_keys_that_are_absent() -> None:
    text, changed = merge_ai_lines("PKOS_PORT=8000\n", {"PKOS_AI_MODEL": '"m1"'})

    assert text.splitlines() == ["PKOS_PORT=8000", "PKOS_AI_MODEL=\"m1\""]
    assert changed == ("PKOS_AI_MODEL",)


def test_merge_replaces_every_duplicate_line() -> None:
    """dotenv 里后出现的覆盖前面的。只改第一条的话，
    那条陈旧的重复行会赢，而界面显示"已保存新值"——最难排查的一类假成功。

    两行都留下、都换新值，而不是删掉一行：**删行是在动用户写的东西**，
    而留一行的代价只是文件里多一条冗余设置。
    """
    text, _ = merge_ai_lines(
        'PKOS_AI_MODEL="旧的值"\n别的行\nPKOS_AI_MODEL="更旧的值"\n',
        {"PKOS_AI_MODEL": '"新的值"'},
    )

    assert "旧的" not in text
    assert text.count('PKOS_AI_MODEL="新的值"') == 2


def test_merge_handles_export_prefix() -> None:
    """认得 `export KEY=` 这种写法。替换后统一成普通写法：
    应用读的键名不变，少一种拼法就少一处将来会看错的东西。"""
    text, changed = merge_ai_lines("export PKOS_AI_MODEL=old\n", {"PKOS_AI_MODEL": '"new"'})

    assert text == 'PKOS_AI_MODEL="new"\n'
    assert changed == ("PKOS_AI_MODEL",)


def test_merge_preserves_crlf_line_endings() -> None:
    """行尾不该被顺手改掉：那等于"只改 6 行"却重写了用户整个文件。"""
    text, _ = merge_ai_lines("PKOS_PORT=8000\r\nPKOS_AI_MODEL=old\r\n", {"PKOS_AI_MODEL": '"m"'})
    assert "\r\n" in text
    assert text.count("\n") == text.count("\r\n")


def test_merge_from_nothing_creates_the_keys() -> None:
    text, changed = merge_ai_lines(None, {"PKOS_AI_JSON_MODE": "false"})
    assert text == "PKOS_AI_JSON_MODE=false\n"
    assert changed == ("PKOS_AI_JSON_MODE",)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://x/v1", '"https://x/v1"'),
        (True, "true"),
        (False, "false"),
        (60, "60"),
        # bool 必须在 int 之前判断：Python 里 True 就是 1，写进 .env 会变成 1
        ('带 "引号" 和 空格', '"带 \\"引号\\" 和 空格"'),
        ("含 # 井号", '"含 # 井号"'),
    ],
)
def test_render_value(value: object, expected: str) -> None:
    assert render_value(value) == expected


# ================================================================ 写文件的规矩


def test_update_writes_file_and_backs_up_the_original(ai_env: Path) -> None:
    env = ai_env
    env.write_text("PKOS_PORT=8000\nPKOS_AI_MODEL=old\n", encoding="utf-8")

    result = update_ai_config(current_settings(), {"model": "新的模型"})

    assert "PKOS_PORT=8000" in env.read_text(encoding="utf-8"), "别的键必须原样保留"
    assert 'PKOS_AI_MODEL="新的模型"' in env.read_text(encoding="utf-8")
    assert result.changed_keys == ["PKOS_AI_MODEL"]
    assert result.backup_file is not None

    backup = Path(result.backup_file)
    assert backup.parent == get_settings().dotenv_backup_dir
    assert backup.read_text(encoding="utf-8") == "PKOS_PORT=8000\nPKOS_AI_MODEL=old\n"


def test_update_creates_missing_file_without_a_backup(ai_env: Path) -> None:
    result = update_ai_config(current_settings(), BASE_BODY)

    assert ai_env.exists()
    assert result.backup_file is None
    assert "新建" in result.message


def test_update_writes_only_the_fields_that_were_given(ai_env: Path) -> None:
    """PUT 的语义是"没传就不改"。这里直接证明文件里那一行没被动过。"""
    ai_env.write_text('PKOS_AI_BASE_URL="https://keep.me/v1"\n', encoding="utf-8")

    update_ai_config(current_settings(), {"model": "只改模型名"})

    assert 'PKOS_AI_BASE_URL="https://keep.me/v1"' in ai_env.read_text(encoding="utf-8")


def test_update_takes_effect_without_restarting(ai_env: Path) -> None:
    """⚠️ "方案 A 需要重启"这一条在这里被实测否定了：
    写完清掉配置缓存，而 Provider 每请求新建，所以下一个请求就用新值。
    界面上因此可以写"已保存并生效"，而不是让用户去重启。"""
    update_ai_config(current_settings(), BASE_BODY)

    settings = get_settings()
    assert settings.ai_base_url == "https://api.deepseek.com/v1"
    assert settings.ai_model == "deepseek-chat"
    assert is_configured(settings) is True


def test_shadowed_env_var_is_reported_not_hidden(ai_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实环境变量优先于 .env。写了不生效却不说，界面就成了一台说谎的机器。"""
    monkeypatch.setenv("PKOS_AI_MODEL", "来自进程环境的模型名")

    result = update_ai_config(current_settings(), {"model": "文件里的模型名"})

    assert result.takes_effect_now is False
    assert "PKOS_AI_MODEL" in result.config.shadowed_keys
    assert "环境变量" in result.message


def test_no_temp_file_left_behind(ai_env: Path) -> None:
    """原子替换靠 `os.replace`：中途失败也不该在目录里留下半截临时文件。"""
    update_ai_config(current_settings(), BASE_BODY)
    assert list(ai_env.parent.glob(f"{ai_env.name}.tmp-*")) == []


def test_write_goes_through_temp_then_replace(ai_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 直接 `path.write_text()` 也能让上面那条测试通过——所以这条测的是**机制**。

    "先写临时文件、再原子替换"这个属性从外面看不出来（除非恰好在中间断电），
    变异测试一删掉它就是存活变异体。这里记录 os.replace 的调用，
    把"不存在半截 .env 的中间状态"变成一条真的会被测到的性质。
    """
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def record(src: str, dst: str) -> str:
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", record)
    update_ai_config(current_settings(), BASE_BODY)

    assert len(calls) == 1, "写 .env 必须走一次原子替换"
    temp, target = calls[0]
    assert Path(target) == ai_env
    assert Path(temp) != ai_env, "临时文件必须是另一个名字"


def test_backups_are_rotated(ai_env: Path) -> None:
    """每个副本都是一个泄露面，所以只留最新 BACKUP_KEEP 份。"""
    for index in range(BACKUP_KEEP + 3):
        ai_env.write_text(f"PKOS_AI_MODEL=第{index}次\n", encoding="utf-8")
        update_ai_config(current_settings(), {"model": f"model-{index}"})

    backups = sorted(get_settings().dotenv_backup_dir.glob("*.bak"))
    assert len(backups) == BACKUP_KEEP


def test_unreadable_encoding_refuses_to_touch_the_file(ai_env: Path) -> None:
    """Notepad 把带中文注释的 .env 另存为 ANSI(GBK) 是真实场景。
    猜一个编码读进来再按 UTF-8 写回去 = 用户的注释整片变乱码，而且是默默坏掉。

    这里用 `Settings(_env_file=None)`：不读那个坏文件也能构造配置，
    否则测试会在"读配置"这一步就炸，测不到写文件这一层的守卫。
    """
    original = "PKOS_PORT=8000 # 端口设置\n".encode("gbk")
    ai_env.write_bytes(original)

    with pytest.raises(DotenvUnreadable) as excinfo:
        update_ai_config(Settings(_env_file=None), {"model": "m"})

    assert ai_env.read_bytes() == original, "读不出就不能改：这个文件是用户手工编辑的"
    assert "UTF-8" in str(excinfo.value)


def test_broken_config_file_says_what_to_do(ai_env: Path) -> None:
    """读不出配置文件时，报的是"哪个文件、怎么办"，不是裸的 UnicodeDecodeError。

    这条路径在启动阶段就会撞上：进程直接拒绝启动，控制台上是同一句话。
    对一个要自己折腾 .env 的新手来说，"另存为 UTF-8"这一句就是全部意义。
    """
    ai_env.write_bytes("PKOS_AI_TIMEOUT_SECONDS=60秒 # 端口\n".encode("gbk"))

    with pytest.raises(ConfigFileError) as excinfo:
        get_settings()

    assert "UTF-8" in str(excinfo.value)
    assert str(ai_env) in str(excinfo.value)


def test_invalid_config_value_reports_field_not_value(ai_env: Path) -> None:
    """⚠️ 值不合法时，报错只说**字段名**，绝不回显值（C6）：
    Pydantic 的 errors() 里 `input` 就是用户填的原文，可能是 API Key。"""
    ai_env.write_text("PKOS_AI_TIMEOUT_SECONDS=不是数字\n", encoding="utf-8")

    with pytest.raises(ConfigFileError) as excinfo:
        get_settings()

    assert "ai_timeout_seconds" in str(excinfo.value)
    assert "不是数字" not in str(excinfo.value)


def test_curly_quoted_key_gets_a_plain_language_error(ai_env: Path) -> None:
    """真机联调挖出的真实事故：中文输入法把引号敲成「“”」，
    python-dotenv 不认识，原样进了值，最后在 httpx 拼 Authorization 头时
    炸成没人看得懂的 UnicodeEncodeError → 500。
    现在配置层就拦下，报大白话。报错里只有字段名，没有密钥内容（C6）。"""
    ai_env.write_text('PKOS_AI_API_KEY=“sk-test-abc12345”\n', encoding="utf-8")

    with pytest.raises(ConfigFileError) as excinfo:
        get_settings()

    message = str(excinfo.value)
    assert "ai_api_key" in message
    assert "非英文字符" in message
    assert "sk-test-abc12345" not in message


def test_non_ascii_anywhere_in_the_key_is_rejected_not_just_at_the_front(ai_env: Path) -> None:
    """⑨变异测试探出来的缺口：只测「引号在开头」的话，
    把校验弱化成只看首字符也能蒙混过关。夹在中间的中文引号
    同样会在 httpx 拼 Authorization 头时炸成 500，一样要拦。
    （结尾的全角空格不算这类：strip 本来就会把它当空白剥掉，单独测。）"""
    ai_env.write_text('PKOS_AI_API_KEY=sk-test“abc12345\n', encoding="utf-8")
    with pytest.raises(ConfigFileError) as excinfo:
        get_settings()
    message = str(excinfo.value)
    assert "ai_api_key" in message
    assert "非英文字符" in message
    assert "sk-test-abc12345" not in message


def test_trailing_full_width_space_is_stripped_not_rejected(ai_env: Path) -> None:
    """中文输入法敲空格经常变成全角「　」。它属于空白，剥掉即可，
    不该和乱码符号一起被当成错误——用户只是多敲了一下空格。"""
    ai_env.write_text('PKOS_AI_API_KEY=sk-test-abc12345　\n', encoding="utf-8")

    assert get_settings().ai_api_key == "sk-test-abc12345"


def test_surrounding_whitespace_in_ai_values_is_stripped(ai_env: Path) -> None:
    """等号后多打一个空格是新手第二常见事故。两端空白对
    地址/密钥/模型名都没有意义，剥掉而不是报错。"""
    ai_env.write_text('PKOS_AI_BASE_URL= https://api.example.com/v1 \n', encoding="utf-8")

    assert get_settings().ai_base_url == "https://api.example.com/v1"


# ================================================================ 读配置的规矩


def test_config_view_never_shows_the_full_key(ai_env: Path) -> None:
    view = get_ai_config(Settings(ai_api_key=SENTINEL_KEY))

    assert view.api_key_set is True
    assert SENTINEL_KEY not in view.api_key_hint
    assert view.api_key_hint == f"…{SENTINEL_KEY[-4:]}"


def test_short_key_is_not_partial_echoed(ai_env: Path) -> None:
    """长度不到 8 时末 4 位就占了大半，等于直接泄露——干脆不显示。"""
    view = get_ai_config(Settings(ai_api_key="abc123"))
    assert view.api_key_hint == "…（已设置，内容太短不显示）"


def test_missing_key_has_no_hint(ai_env: Path) -> None:
    assert get_ai_config(Settings()).api_key_hint is None


def test_base_url_without_v1_gets_a_hint(ai_env: Path) -> None:
    """D-044 那个坑（少了 /v1 → 400）提前在设置页说出来，
    比让用户对着"请求被拒绝"猜半天划算。"""
    without = get_ai_config(Settings(ai_base_url="https://api.deepseek.com"))
    with_version = get_ai_config(Settings(ai_base_url="https://api.deepseek.com/v1"))
    empty = get_ai_config(Settings())

    assert "/v1" in without.hint
    assert with_version.hint is None
    assert empty.hint is None


def test_configured_and_key_set_are_two_different_facts(ai_env: Path) -> None:
    """本地 Ollama 的合法状态：configured 为真、根本没有 key 这回事。"""
    view = get_ai_config(Settings(ai_base_url="http://127.0.0.1:11434/v1", ai_model="qwen2.5"))

    assert view.configured is True
    assert view.api_key_set is False


# ================================================================ HTTP 接口


def test_get_settings_masks_the_key(env_client: TestClient) -> None:
    response = env_client.get("/api/settings/ai")
    body = response.json()

    assert response.status_code == 200
    assert body["api_key_set"] is False
    assert body["api_key_hint"] is None
    assert body["configured"] is False
    assert SENTINEL_KEY not in response.text


def test_put_writes_env_and_echoes_nothing_secret(env_client: TestClient, ai_env: Path) -> None:
    response = env_client.put("/api/settings/ai", json={**BASE_BODY, "api_key": SENTINEL_KEY})
    assert response.status_code == 200, response.text

    body = response.json()
    # 顺序取决于"文件里已有 / 需要新增"，所以按集合比：
    # 要钉住的是**这三项都写进去了**，不是它们排在第几位。
    assert set(body["changed_keys"]) == {
        "PKOS_AI_BASE_URL",
        "PKOS_AI_MODEL",
        "PKOS_AI_API_KEY",
    }
    assert body["config"]["api_key_hint"] == f"…{SENTINEL_KEY[-4:]}"
    assert SENTINEL_KEY not in response.text
    assert SENTINEL_KEY in ai_env.read_text(encoding="utf-8"), "密钥应该真的写进文件"

    reloaded = env_client.get("/api/settings/ai").json()
    assert reloaded["base_url"] == "https://api.deepseek.com/v1"
    assert reloaded["api_key_set"] is True
    assert SENTINEL_KEY not in env_client.get("/api/settings/ai").text, "写进去就读不回来"


def test_put_writes_json_mode_as_lowercase_true(env_client: TestClient, ai_env: Path) -> None:
    """pydantic-settings 解析 bool 只认小写的 true/false。
    写成 Python 的 True 会让下一次启动直接校验失败——服务连不上比慢一点糟得多。"""
    env_client.put("/api/settings/ai", json={**BASE_BODY, "json_mode": True})
    body = env_client.get("/api/settings/ai").json()

    assert body["json_mode"] is True
    assert "PKOS_AI_JSON_MODE=true" in ai_env.read_text(encoding="utf-8")


def test_put_rejects_an_empty_body(env_client: TestClient) -> None:
    """空请求体必须拒绝：返回 200 会让用户以为"保存成功"，其实什么都没写。"""
    assert env_client.put("/api/settings/ai", json={}).status_code == 422


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        ({"base_url": "api.deepseek.com/v1"}, "http://"),
        ({"base_url": "ftp://x.example.com"}, "http://"),
        ({"base_url": "https://x"}, None),
        ({"api_key": "short"}, "at least 8 characters"),
        ({"timeout_seconds": 2}, "greater than or equal to 5"),
        ({"timeout_seconds": 9999}, "less than or equal to 600"),
        ({"unknown_key": "x"}, "unknown_key"),
    ],
)
def test_put_validates_at_the_boundary(
    env_client: TestClient, payload: dict, expected_fragment: str | None
) -> None:
    """这些都要在边界上就挡住，而不是写坏文件、或者到 SQLite 才炸成 500。

    `https://x` 那条是刻意**放过**的：这里只挡"一眼就不是地址"的输入，
    不去猜端点对不对——真正的验证是"测试连接"按钮发一次真请求。
    """
    response = env_client.put("/api/settings/ai", json=payload)

    if expected_fragment is None:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 422, response.text
        assert expected_fragment in response.text


def test_put_uses_the_http_409_when_the_file_is_unreadable(env_client: TestClient, ai_env: Path) -> None:
    """不是 500：程序没坏，是文件编码需要人来处理，而该做什么都写在 detail 里。"""
    ai_env.write_bytes("PKOS_PORT=8000 # 端口设置\n".encode("gbk"))

    response = env_client.put("/api/settings/ai", json={"model": "m"})
    assert response.status_code == 409
    assert "UTF-8" in response.json()["detail"]


def test_settings_write_is_not_logged_with_the_key(
    env_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """日志不得记录完整密钥（Blueprint 9.1）。这里连备份路径都不带 key。"""
    with caplog.at_level(logging.DEBUG):
        env_client.put("/api/settings/ai", json={**BASE_BODY, "api_key": SENTINEL_KEY})

    assert SENTINEL_KEY not in caplog.text


def test_health_reflects_the_new_config_without_restarting(env_client: TestClient) -> None:
    """阶段 0 的 `ai_configured` 恒为 false，因为它只看 key；
    现在它和 factory 用同一个判据，所以填完地址和模型名就变 true。"""
    assert env_client.get("/api/health").json()["ai_configured"] is False
    env_client.put("/api/settings/ai", json=BASE_BODY)

    assert env_client.get("/api/health").json()["ai_configured"] is True


def test_connection_test_reports_success(probe_env: None) -> None:
    del probe_env  # 夹具的作用只是把临时 .env、迁移和引擎准备好
    client = make_probe_client(StubProvider(chunks=["收", "到"]))

    response = client.post("/api/settings/ai/test")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["ok"] is True
    assert body["error_kind"] is None
    assert body["model"] == "stub-model"


def test_connection_test_reports_failure_as_a_result_not_an_http_error(
    probe_env: None,
) -> None:
    """200 + ok:false：这次"检查"本身成功了，失败的是被检查的那条链路。
    把它做成 502 会让前端的通用错误处理弹一个"服务异常"，反而说不清该改什么。"""
    del probe_env
    client = make_probe_client(StubProvider(error=AiUnavailable()))

    body = client.post("/api/settings/ai/test").json()

    assert body["ok"] is False
    assert body["error_kind"] == "unavailable"
    assert body["message"] == AiUnavailable.user_message


def test_connection_test_without_configuration_is_409(env_client: TestClient) -> None:
    """连 Provider 都构造不出来时不该发任何请求，直接说该去填哪两项。"""
    response = env_client.post("/api/settings/ai/test")

    assert response.status_code == 409
    assert set(response.json()["missing"]) == {"PKOS_AI_BASE_URL", "PKOS_AI_MODEL"}


def test_settings_page_hint_is_served_to_the_ui(env_client: TestClient) -> None:
    env_client.put("/api/settings/ai", json={"base_url": "https://api.deepseek.com", "model": "m"})
    body = env_client.get("/api/settings/ai").json()

    assert "/v1" in body["hint"]


def test_the_file_we_write_is_the_file_the_app_reads(ai_env: Path) -> None:
    """`ENV_FILE` 和 pydantic-settings 读的必须指向同一个文件。

    写 A 读 B 的话，界面上"保存成功"改的根本不是启动时读的那份——
    这条约束本身没有测试的话，改配置加载方式时很容易悄悄破坏它。
    """
    settings = get_settings()
    assert settings.dotenv_path == ai_env
    assert Settings.model_config["env_file"] == ai_env

    update_ai_config(settings, {"model": "只有模型名"})

    assert 'PKOS_AI_MODEL="只有模型名"' in ai_env.read_text(encoding="utf-8")
    assert get_settings().ai_model == "只有模型名"
