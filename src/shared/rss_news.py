"""
Free, public RSS news feeds for Indian stock market headlines — no API key needed.
Separate from Yahoo Finance's per-symbol `.news` (market_data_cache.get_news);
these are general market-wide headlines from Indian financial news outlets.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

from .service_log import log_event

_FEEDS: dict[str, str] = {
    "economic_times": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "livemint":       "https://www.livemint.com/rss/markets",
    "cnbctv18":       "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
    "ndtv_profit":    "https://feeds.feedburner.com/ndtvprofit-latest",
}
# moneycontrol, zee business/news dropped — their CDN (Akamai) consistently returns 403
# to non-browser clients. business_today/india_today/news18 dropped — wrong topic or
# malformed XML on the feeds tested.

_HEADERS = {"User-Agent": "Mozilla/5.0 (MoneyMakerNewsBot/1.0)"}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


async def fetch_feed(source: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and parse one RSS feed. Returns [] on any failure (network/parse).

    Retries once — cnbctv18's feed alone is ~200KB (vs ~30-40KB for the others),
    and the original 10s timeout was tight enough that a single slow round-trip
    looked like a permanent failure rather than the transient blip it usually was."""
    url = _FEEDS.get(source)
    if not url:
        return []
    last_error = ""
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    text = await resp.text()
            root = ET.fromstring(text)
            items = [
                {
                    "title":    (item.findtext("title") or "").strip(),
                    "summary":  _strip_html(item.findtext("description") or "")[:500],
                    "link":     item.findtext("link") or "",
                    "pub_date": item.findtext("pubDate") or "",
                    "source":   source,
                }
                for item in root.findall(".//item")[:limit]
            ]
            headline_preview = f" — \"{items[0]['title'][:90]}\"" if items else ""
            log_event("news", "info", f"Fetched {len(items)} headlines: {source}{headline_preview}")
            return items
        except Exception as exc:
            last_error = repr(exc) or type(exc).__name__
            if attempt == 0:
                await asyncio.sleep(2)
    log_event("news", "error", f"Feed fetch failed: {source}", {"error": last_error})
    return []


async def fetch_all_feeds(limit_per_feed: int = 5) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source in _FEEDS:
        items.extend(await fetch_feed(source, limit_per_feed))
    return items


def list_sources() -> list[str]:
    return list(_FEEDS.keys())
