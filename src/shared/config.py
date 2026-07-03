"""
Typed configuration loader — reads config.toml + env vars.
Import `settings` anywhere; it's a singleton.
"""
from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-reattr]

from pydantic import field_validator
from pydantic_settings import BaseSettings


_SENSITIVE_FIELDS = frozenset({
    "azure_openai_api_key", "secret_key", "api_secret_key",
    "five_paisa_password", "five_paisa_totp_secret", "five_paisa_password_key",
    "five_paisa_encryption_key", "reddit_client_secret",
})


class Settings(BaseSettings):
    # ── System ────────────────────────────────────────────────────────────
    env:           str   = "sandbox"
    log_level:     str   = "INFO"
    timezone:      str   = "Asia/Kolkata"
    secret_key:    str   = ""
    api_secret_key: str  = ""

    # ── Azure OpenAI ──────────────────────────────────────────────────────
    azure_openai_api_key:   str = ""
    azure_openai_endpoint:  str = ""
    azure_openai_api_version: str = "2024-05-01-preview"

    @field_validator("secret_key")
    @classmethod
    def secret_key_must_not_be_default(cls, v: str) -> str:
        if v in ("", "change_me"):
            warnings.warn(
                "SECRET_KEY is not set or is the default 'change_me'. "
                "Set SECRET_KEY in .env before going to production.",
                stacklevel=2,
            )
        return v

    # ── Azure Key Vault (optional — one secret holds every credential below) ─
    azure_key_vault_url:         str = ""
    azure_key_vault_secret_name: str = "KDmonk"

    # ── 5Paisa ────────────────────────────────────────────────────────────
    five_paisa_client_code:   str = ""
    five_paisa_password:      str = ""
    five_paisa_dob:           str = ""
    five_paisa_totp_secret:   str = ""
    five_paisa_app_name:      str = ""
    five_paisa_user_id:       str = ""
    five_paisa_password_key:  str = ""
    five_paisa_encryption_key: str = ""
    five_paisa_app_source:    str = "1234"

    # ── Reddit (sentiment feed) ───────────────────────────────────────────
    reddit_client_id:     str = ""
    reddit_client_secret: str = ""
    reddit_user_agent:    str = "MoneyMaker/1.0"

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./moneymaker.db"

    # ── Redis ─────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── API ───────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    def __repr__(self) -> str:
        fields = self.model_dump()
        for k in _SENSITIVE_FIELDS:
            if fields.get(k):
                fields[k] = "***"
        return f"Settings({fields})"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()  # .env + real env vars, exactly as before
    if s.azure_key_vault_url:
        from .azure_secrets import get_secret_json
        creds = get_secret_json(s.azure_key_vault_secret_name, s.azure_key_vault_url)
        if creds:
            # Only fills in fields .env left blank — anything already set in
            # .env (or a real exported env var) keeps its value untouched.
            # That means credentials can move into the vault one at a time;
            # nothing breaks for whatever hasn't moved yet.
            for env_key, value in creds.items():
                field_name = env_key.lower()
                if hasattr(s, field_name) and not getattr(s, field_name):
                    setattr(s, field_name, str(value))
    return s


def load_toml_config(path: Path | None = None) -> dict:
    if path is None:
        path = Path(__file__).parent.parent.parent / "config.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


settings = get_settings()
toml_cfg = load_toml_config()
