"""
5Paisa TOTP-based authentication with automatic token refresh.

The 5Paisa session token is valid for the rest of the calendar day it was
issued. Logging in repeatedly risks the account getting blocked, so the
token is cached to disk and reused across backend restarts on the same day
instead of calling get_totp_session() again.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

import pyotp
import structlog

from ...shared.config import settings
from ...shared.service_log import log_event

log = structlog.get_logger(__name__)

# Token TTL in seconds (5Paisa tokens expire after ~24 hours)
_TOKEN_TTL = 82_800  # 23 hours, refresh before expiry

_SESSION_FILE = Path(__file__).resolve().parents[3] / ".five_paisa_session.json"


class FivePaisaAuth:
    def __init__(self) -> None:
        self._client: Optional[object] = None
        self._token_fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_client(self):  # type: ignore[return]
        """Return an authenticated py5paisa client, refreshing if needed."""
        async with self._lock:
            if self._client is None or self._is_token_expired():
                await self._login()
            return self._client

    def _is_token_expired(self) -> bool:
        return (time.monotonic() - self._token_fetched_at) > _TOKEN_TTL

    @staticmethod
    def _load_cached_session() -> Optional[dict]:
        if not _SESSION_FILE.exists():
            return None
        try:
            data = json.loads(_SESSION_FILE.read_text())
        except Exception:
            return None
        if data.get("date") != date.today().isoformat():
            return None
        return data

    @staticmethod
    def _save_session(access_token: str, client_code: str) -> None:
        _SESSION_FILE.write_text(json.dumps({
            "date": date.today().isoformat(),
            "access_token": access_token,
            "client_code": client_code,
        }))

    async def _login(self) -> None:
        try:
            from py5paisa import FivePaisaClient  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "py5paisa not installed. Run: pip install py5paisa"
            )

        cred = {
            "APP_NAME":       settings.five_paisa_app_name,
            "APP_SOURCE":     settings.five_paisa_app_source,
            "USER_ID":        settings.five_paisa_user_id,
            "PASSWORD":       settings.five_paisa_password_key,
            "USER_KEY":       settings.five_paisa_app_name,
            "ENCRYPTION_KEY": settings.five_paisa_encryption_key,
        }
        client = FivePaisaClient(cred=cred)

        cached = self._load_cached_session()
        if cached and cached.get("access_token"):
            client.set_access_token(cached["access_token"], cached["client_code"])
            self._client = client
            self._token_fetched_at = time.monotonic()
            log.info("five_paisa.auth.reused_cached_session")
            log_event("five_paisa", "info", "Reused cached session token (same day)")
            return

        totp = pyotp.TOTP(settings.five_paisa_totp_secret).now()
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.get_totp_session(
                settings.five_paisa_client_code,
                totp,
                settings.five_paisa_password,
            ),
        )
        self._client = client
        self._token_fetched_at = time.monotonic()

        token = getattr(client, "access_token", None)
        if token:
            self._save_session(token, settings.five_paisa_client_code)

        log.info("five_paisa.auth.logged_in", client_code=settings.five_paisa_client_code)
        log_event(
            "five_paisa", "info", "Fresh TOTP login",
            {"client_code": settings.five_paisa_client_code},
        )
