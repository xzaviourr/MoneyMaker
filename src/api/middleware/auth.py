"""
API key authentication middleware.

When API_SECRET_KEY is set in .env, every request must include:
    X-Api-Key: <value>

Exemptions (no key needed):
- GET /health          — uptime checks
- /ws/live             — WebSocket handshake (browsers cannot send custom headers)

If API_SECRET_KEY is empty the middleware is a no-op, so local dev
without any .env setup continues to work unchanged.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...shared.config import settings

_EXEMPT_PATHS = {"/health", "/ws/live"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        key = settings.api_secret_key
        if not key:
            return await call_next(request)

        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        provided = request.headers.get("X-Api-Key", "")
        if provided != key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
