from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    anthropic_api_key: str = ""
    fallback_provider: str = "gemini"
    fallback_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str = ""

    cache_ttl_seconds: int = 3600
    fetch_timeout_seconds: float = 10.0
    max_html_bytes: int = 2_000_000
    max_content_chars: int = 12_000   # ~3-4k tokens sent to the LLM
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 3000


@lru_cache
def get_settings() -> Settings:
    return Settings()
