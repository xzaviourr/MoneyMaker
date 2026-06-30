"""
Reddit sentiment feed — retail chatter from Indian-market subreddits, read
through PRAW. Same role as rss_news.py (a market-wide text source feeding
NewsWatchdog), just noisier and requires its own API key.

Needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in .env (free, from
reddit.com/prefs/apps, app type "script"). Without them this module no-ops —
same pattern as 5Paisa being optional until its credentials are present.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import structlog

from .config import settings
from .service_log import log_event

log = structlog.get_logger(__name__)

# This Reddit app is shared with another, unrelated project — stay well under
# Reddit's own limits AND self-throttle so our usage is never the reason it
# gets flagged. Read-only, max ~2-3 requests/minute, spaced out, not bursty.
_SUBREDDITS = ["IndianStreetBets", "IndiaInvestments", "DalalStreetTalks"]
_MIN_SECONDS_BETWEEN_REQUESTS = 25
_last_request_at: float = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_SECONDS_BETWEEN_REQUESTS:
        time.sleep(_MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
    _last_request_at = time.monotonic()

_client: Optional[Any] = None
_warned_missing_creds = False


def _get_client() -> Optional[Any]:
    global _client, _warned_missing_creds
    if _client is not None:
        return _client

    if not settings.reddit_client_id or not settings.reddit_client_secret:
        if not _warned_missing_creds:
            log_event("reddit", "warning",
                      "Reddit not connected — REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET missing from .env")
            _warned_missing_creds = True
        return None

    try:
        import praw
        _client = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
            ratelimit_seconds=300,  # let PRAW sleep+retry on Reddit's own rate-limit signal
        )
        _client.read_only = True  # this app is shared elsewhere — never write/vote/post
        log_event("reddit", "info", "Reddit client connected (read-only)")
        return _client
    except Exception as exc:
        log.error("reddit_feed.connect_failed", error=str(exc))
        log_event("reddit", "error", "Reddit connection failed", {"error": str(exc)})
        return None


def fetch_subreddit_posts(limit_per_sub: int = 10) -> list[dict]:
    """Sync — call via run_in_executor from async code, same as other PRAW/yfinance calls."""
    client = _get_client()
    if client is None:
        return []

    posts: list[dict] = []
    for sub_name in _SUBREDDITS:
        _throttle()
        try:
            for post in client.subreddit(sub_name).new(limit=limit_per_sub):
                posts.append({
                    "title":    post.title,
                    "selftext": (post.selftext or "")[:500],
                    "url":      f"https://reddit.com{post.permalink}",
                    "subreddit": sub_name,
                    "score":    post.score,
                    "created_utc": post.created_utc,
                })
        except Exception as exc:
            log_event("reddit", "error", f"Fetch failed: r/{sub_name}", {"error": str(exc)})

    if posts:
        preview = f" — \"{posts[0]['title'][:90]}\""
        log_event("reddit", "info", f"Fetched {len(posts)} posts across {len(_SUBREDDITS)} subreddits{preview}")
    return posts


def list_subreddits() -> list[str]:
    return list(_SUBREDDITS)


def rate_limit_status() -> dict:
    """Exposed for the Flow dashboard — so the rate limiter is visible, not just trusted."""
    per_cycle_seconds = _MIN_SECONDS_BETWEEN_REQUESTS * (len(_SUBREDDITS) - 1)
    return {
        "subreddit_count":            len(_SUBREDDITS),
        "min_seconds_between_requests": _MIN_SECONDS_BETWEEN_REQUESTS,
        "seconds_since_last_request": round(time.monotonic() - _last_request_at, 1) if _last_request_at else None,
        "read_only": bool(_client.read_only) if _client is not None else None,
        "connected": _client is not None,
    }
