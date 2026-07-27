"""
Centralized settings. Everything secret or environment-specific comes from env vars.
Never hardcode API keys or model IDs here — see docs/rule.md Security Rules > Secrets.
"""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- NVIDIA NIM ---
    nvidia_api_key: str = Field(..., description="NVIDIA NIM API key (NVAPI key)")
    nim_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    nim_model_small: str = Field(
        ..., description="Cheap/fast NIM model id for PLAN/RETRIEVE/FORMAT steps"
    )
    nim_model_large: str = Field(
        ..., description="Larger NIM model id for the REASON step"
    )
    nim_timeout_seconds: float = Field(default=20.0)
    nim_max_retries: int = Field(default=2)

    # --- App ---
    environment: str = Field(default="development")  # development | staging | production
    log_level: str = Field(default="INFO")

    # --- Security ---
    api_keys: str = Field(
        default="", description="Comma-separated allowed client API keys"
    )
    rate_limit_query_per_min: int = Field(default=20)
    rate_limit_upload_per_min: int = Field(default=5)
    max_query_chars: int = Field(default=4000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)  # 10MB
    allowed_upload_extensions: str = Field(default=".pdf,.txt,.md,.csv")
    upload_dir: str = Field(
        default="/var/lib/app/uploads",
        description="Non-web-servable, non-predictable-path storage for uploads",
    )

    @property
    def allowed_api_keys(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def allowed_upload_ext_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_upload_extensions.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    # Fails fast at startup (not on first request) if required vars are missing.
    return Settings()
