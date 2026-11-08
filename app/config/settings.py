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

    runtime_max_steps: int = Field(default=8, ge=1, le=100)
    runtime_max_context_chars: int = Field(default=48000, ge=1)
    runtime_max_requests_per_session: int = Field(default=500, ge=1, le=10000)

    external_max_concurrent: int = Field(default=4, ge=1, le=100)
    external_max_queue: int = Field(default=16, ge=0, le=1000)
    external_queue_timeout_seconds: float = Field(default=5, gt=0, le=180)
    llm_max_concurrent: int = Field(default=2, ge=1, le=100)

    asr_provider: Literal["disabled", "tencent"] = "disabled"
    tencent_secret_id: SecretStr = SecretStr("")
    tencent_secret_key: SecretStr = SecretStr("")
    tencent_token: SecretStr = SecretStr("")
    asr_engine: Literal["16k_zh", "16k_zh-PY", "16k_en", "16k_yue"] = "16k_zh"
    asr_timeout_seconds: float = Field(default=20, gt=0, le=120)
    asr_max_concurrent: int = Field(default=2, ge=1, le=10)
    asr_max_requests: int = Field(default=100, ge=1, le=1000)
    asr_cache_seconds: float = Field(default=600, ge=1, le=3600)

    @property
    def asr_configured(self) -> bool:
        return self.asr_provider == "tencent" and bool(
            self.tencent_secret_id.get_secret_value().strip()
            and self.tencent_secret_key.get_secret_value().strip()
        )

    @property
    def key_configured(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value().strip())


def get_settings() -> Settings:
    # 每次读取本地配置，修改 Key 后无需重启服务。
    return Settings()
