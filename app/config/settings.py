from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: AnyHttpUrl = "https://api.deepseek.com"
    deepseek_model: str = Field(default="deepseek-flash", min_length=1)
    llm_timeout_seconds: float = Field(default=45, gt=0, le=180)
    llm_max_tokens: int = Field(default=800, ge=1, le=4096)
    llm_thinking: Literal["enabled", "disabled"] = "disabled"

    psychology_enabled: bool = False
    analysis_timezone: str = "Asia/Shanghai"

    runtime_max_steps: int = Field(default=8, ge=1, le=100)
    runtime_max_context_chars: int = Field(default=48000, ge=1)
    runtime_max_requests_per_session: int = Field(default=500, ge=1, le=10000)

    @property
    def key_configured(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value().strip())


def get_settings() -> Settings:
    # 每次读取本地配置，修改 Key 后无需重启服务。
    return Settings()
