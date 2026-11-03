from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", populate_by_name=True)
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY", "DOUBAO_API_KEY")
    )
    openai_base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        validation_alias=AliasChoices("OPENAI_BASE_URL", "DOUBAO_BASE_URL"),
    )
    openai_model: str = Field(
        default="doubao-lite-2.1", validation_alias=AliasChoices("OPENAI_MODEL", "DOUBAO_MODEL")
    )
    openai_enable_thinking: bool | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "lifeos-agent-dev"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    lifeos_db: str = "data/lifeos.sqlite3"
    mcp_url: str = "http://127.0.0.1:8001/mcp"
    mcp_port: int = 8001
    mcp_timeout_seconds: float = 15
    delivery_scenario: Literal[
        "success", "rejected", "pending", "lost_response", "unknown", "payment_failed"
    ] = "success"

    @property
    def db_path(self) -> Path:
        path = Path(self.lifeos_db)
        return path if path.is_absolute() else ROOT / path


settings = Settings()
