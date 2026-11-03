"""应用配置。

端口、数据目录、AI 凭据全部来自环境变量或 .env，不在代码里硬编码
（Blueprint 6.4 要求端口写入配置；9.1 要求 API Key 只从环境变量读取）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigFileError(RuntimeError):
    """配置文件读不出来，或里面的值不合法。

    这是**启动级**的坏：应用没法假装继续跑。
    `main.py` 把它映射成一个带可操作说明的响应，
    `scripts/doctor.py` 则提前在自检里报出来。
    """

# 配置文件路径。定在这里而不是写两遍字面量：
# 下面 model_config 读的 .env 和设置页要改的 .env 必须是同一个文件，
# 否则界面上"保存成功"改的根本不是启动时读的那份。
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="PKOS_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    frontend_port: int = 5173

    data_dir: Path | None = None

    ai_provider: str = ""
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 60
    # 让服务端用 JSON mode 输出（response_format={"type":"json_object"}）。
    # 默认关：各家兼容端点对这个字段的支持参差不齐，贸然带上会整批请求 400，
    # 而在用户填入真实 key 之前我们无法验证。校验反正由 Pydantic 兜底（C3）。
    ai_json_mode: bool = False

    log_level: str = "INFO"
    allow_missing_frontend: bool = True

    @field_validator("data_dir", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        # .env 里 PKOS_DATA_DIR= 留空时应回落到默认目录，而不是被解析成 Path(".")
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir) if self.data_dir else PROJECT_ROOT / "data"

    @property
    def dotenv_path(self) -> Path:
        """设置页要读写的那个 .env（见文件顶部 ENV_FILE 的说明）。"""
        return ENV_FILE

    @property
    def dotenv_backup_dir(self) -> Path:
        """.env 的备份目录。刻意放在 data/ 下面而不是项目根目录：

        .gitignore 里有 `data/`，而根目录的 `*.bak` 不在忽略范围内。
        备份文件**含 API Key**，不能依赖"恰好被忽略"这种巧合。
        """
        return self.resolved_data_dir / "env-backups"

    @property
    def backup_dir(self) -> Path:
        return self.resolved_data_dir / "backups"

    @property
    def database_path(self) -> Path:
        return self.resolved_data_dir / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    def ensure_runtime_dirs(self) -> None:
        self.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """读配置。**唯一的**读取入口，所以坏在这里就能统一说清楚。

    两种坏法都不是"程序内部错误"，而是"用户手改的那份文件有问题"，
    而 .env 用 Notepad 另存为 ANSI(GBK) 在 Windows 上是高频事故
    （我们的 .env.example 里全是中文注释）。
    让裸 UnicodeDecodeError 冒出去的话，用户看到的是一行 Python 报错，
    完全不知道该做什么。

    ⚠️ 错误信息里**只出现字段名，绝不出现值**：
    Pydantic 的 `errors()` 里那个 `input` 装的就是用户填的值，
    有可能是 API Key（C6）。
    """
    try:
        return Settings()
    except UnicodeDecodeError as exc:
        raise ConfigFileError(
            f"{ENV_FILE} 不是 UTF-8 文本（{exc.reason}）。"
            "请用编辑器把它另存为 UTF-8 编码后重启，"
            "或者删掉这个文件让它按默认值重新生成。"
        ) from exc
    except ValidationError as exc:
        fields = "、".join(
            sorted({str(part) for error in exc.errors() for part in (error.get("loc") or ())})
        )
        raise ConfigFileError(
            f"配置里有不合法的值：{fields or '（未知字段）'}。"
            f"来源可能是 {ENV_FILE}，也可能是同名环境变量；"
            "请对照 .env.example 里每项的写法改一下再重启。"
        ) from exc


def reset_settings_cache() -> None:
    """设置页写完 .env 之后调用，让下一个请求读到新值。

    单独一个函数而不是让调用方直接碰 `get_settings.cache_clear`：
    lru_cache 的那个方法是实现细节，将来换加载方式时只有一处要改。
    """
    get_settings.cache_clear()
