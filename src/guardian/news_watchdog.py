"""
NewsWatchdog — monitors live news, press wires, SEC/BSE filings for held assets.
Severity levels: INFO / WARNING / EMERGENCY.
Uses STANDARD LLM tier for sentiment analysis.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
import time
from datetime import datetime
from typing import Optional

import aiohttp
import structlog

from ..llm.llm_gateway import LLMGateway
from ..shared import feature_toggles
from ..shared.config import toml_cfg
from ..shared.market_data_cache import get_news
from ..shared.message_bus import MessageBus
from ..shared.reddit_feed import fetch_subreddit_posts
from ..shared.rss_news import fetch_all_feeds
from ..shared.schemas import (
    GuardianAlert,
    GuardianResponseMode,
    LLMTier,
    Message,
    MessageType,
    SignalDirection,
)

log = structlog.get_logger(__name__)

_POLL_INTERVAL        = 60   # seconds — per-symbol Yahoo Finance news
_RSS_POLL_INTERVAL    = 300  # seconds — general market RSS feeds (be polite to free feeds)
_REDDIT_POLL_INTERVAL = 300  # seconds — retail sentiment from finance subreddits
_SYNTHESIS_POLL_INTERVAL = 90    # seconds — how often to check for cross-source overlap
_SYNTHESIS_WINDOW_S      = 1200  # seconds — items older than this don't count as "the same story"
_MAX_WATCHED_SYMBOLS     = 100   # cap on the dynamically-grown per-symbol news watchlist

# Company name → NSE symbol, so a market-wide RSS headline ("Reliance signs deal...")
# can be attributed to a real, tradeable symbol instead of a generic "MARKET" tag.
_COMPANY_ALIASES: dict[str, str] = {
    "reliance industries": "RELIANCE", "reliance": "RELIANCE",
    "tata consultancy services": "TCS", "tcs": "TCS",
    "hdfc bank": "HDFCBANK",
    "icici bank": "ICICIBANK",
    "infosys": "INFY",
    "state bank of india": "SBIN", "sbi": "SBIN",
    "bharti airtel": "BHARTIARTL", "airtel": "BHARTIARTL",
    "kotak mahindra bank": "KOTAKBANK", "kotak bank": "KOTAKBANK",
    "hindustan unilever": "HINDUNILVR", "hul": "HINDUNILVR",
    "larsen & toubro": "LT", "larsen and toubro": "LT", "l&t": "LT",
    "bajaj finance": "BAJFINANCE",
    "axis bank": "AXISBANK",
    "asian paints": "ASIANPAINT",
    "maruti suzuki": "MARUTI", "maruti": "MARUTI",
    "titan company": "TITAN", "titan": "TITAN",
    "sun pharmaceutical": "SUNPHARMA", "sun pharma": "SUNPHARMA",
    "wipro": "WIPRO",
    "ultratech cement": "ULTRACEMCO", "ultratech": "ULTRACEMCO",
    "ntpc": "NTPC",
    "power grid corporation": "POWERGRID", "power grid": "POWERGRID",
    "nestle india": "NESTLEIND", "nestle": "NESTLEIND",
    "mahindra & mahindra": "M&M", "mahindra and mahindra": "M&M",
    "adani enterprises": "ADANIENT",
    "tata motors": "TATAMOTORS",
    "hcl technologies": "HCLTECH", "hcl tech": "HCLTECH",
    "coal india": "COALINDIA",
    "grasim industries": "GRASIM", "grasim": "GRASIM",
    "indusind bank": "INDUSINDBK",
    "oil and natural gas corporation": "ONGC", "ongc": "ONGC",
    "tech mahindra": "TECHM",
}
# Longest aliases first, so "reliance industries" matches before the bare "reliance"
_SORTED_ALIASES = sorted(_COMPANY_ALIASES.items(), key=lambda kv: -len(kv[0]))


def _normalize_headline(title: str) -> str:
    """Lowercase + strip punctuation/extra whitespace, so the same story
    re-published under a slightly different headline (or just a different URL)
    still dedupes correctly."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()

# Public NSE/BSE equity listing (same one 5Paisa uses for order routing) — used here
# only to build a company-name → symbol directory so news about ANY listed company
# (small-cap included, not just the ~30 in our trading universe) gets attributed
# to a real symbol instead of being discarded.
_SCRIP_MASTER_URL = "https://images.5paisa.com/website/scripmaster-csv-format.csv"

_SYSTEM_PROMPT = """You are a financial news analyst specialising in Indian equities.
Assess the following news item, both for a held position AND for someone deciding
whether to buy in fresh. Respond ONLY in JSON:
{
  "severity": "INFO" | "WARNING" | "EMERGENCY",
  "sentiment": "positive" | "negative" | "neutral",
  "recommended_action": "hold" | "reduce" | "exit",
  "stance": "buy" | "avoid" | "watch",
  "rationale": "one-line explanation covering both the action and the stance"
}
EMERGENCY = material adverse event (fraud, regulatory ban, insolvency risk)
WARNING   = significant negative news (guidance cut, major lawsuit, sector headwind)
stance is your buy-side take for someone NOT currently holding this stock:
  "buy"   = this news is itself a reason to consider buying
  "avoid" = this news is a reason to stay away
  "watch" = not significant enough either way
INFO      = routine news or positive sentiment
"""

_SYNTHESIS_SYSTEM_PROMPT = """You are a financial news analyst. You will be given several headlines
about the same Indian-listed stock, each independently spotted by a different source
(Yahoo Finance per-symbol news, general financial RSS, or Reddit retail sentiment).
Decide whether they describe the same underlying story or are unrelated, then give ONE
consolidated view. Respond ONLY in JSON:
{
  "summary": "one or two sentences combining what these sources are actually reporting",
  "recommendation": "buy" | "avoid" | "hold",
  "rationale": "why — explicitly reference which sources agree or disagree"
}
"""

_SYMBOL_EXTRACT_PROMPT = """You identify which NSE-listed Indian company a news headline is about.
Respond ONLY in JSON: {"symbol": "TICKER" | null}
Use the standard NSE ticker (e.g. RELIANCE, TCS, HDFCBANK). If the headline isn't about one
specific NSE-listed company, respond {"symbol": null}.
"""


class NewsWatchdog:
    def __init__(self) -> None:
        self._bus       = MessageBus.get()
        default_universe = toml_cfg.get("long_term_desk", {}).get("universe", [])
        # Watch the full configured universe, not just a handful — news on any
        # of these companies should be able to trigger a trade, not just the top few.
        # A plain dict instead of a set so insertion order is preserved — RSS/Reddit
        # cover the entire ~2,100-symbol NSE directory already (see _extract_symbol);
        # whatever real stock they spot gets added here too via add_symbols(), so
        # Yahoo Finance per-symbol news organically grows to cover anything actually
        # being discussed, not just this fixed starting list. Capped so the per-cycle
        # fetch loop in _check_news() can't grow unbounded and starve itself.
        self._watched_symbols: dict[str, None] = dict.fromkeys(default_universe)
        self._task:        Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._rss_task:    Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._reddit_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._synthesis_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        # Cross-source overlap buffer: the same real-world story often arrives via
        # two or three of {Yahoo Finance per-symbol news, RSS, Reddit} independently,
        # and each pipeline used to analyse + act on it with no idea the others had
        # already seen it. Buffer raw items per symbol for a short window so a
        # periodic pass can spot the overlap and produce one consolidated view.
        self._recent_items: dict[str, list[dict]] = {}
        self._synthesis_feed: list[dict] = []  # most recent first, capped at 20
        # Every single stock-specific article gets a buy/avoid/watch gist here —
        # unlike _synthesis_feed, this does NOT wait for 2+ sources to agree.
        self._gist_feed: list[dict] = []  # most recent first, capped at 30
        self._seen_articles: set[str] = set()
        self._seen_rss: set[str] = set()
        self._seen_reddit: set[str] = set()
        # The same real story often gets re-syndicated under a different URL/AMP
        # link/tracking params — deduping on link alone let it straight through.
        # Catch it by normalized headline text too, scoped per-pipeline (NOT
        # shared across RSS/Reddit/Yahoo) so the cross-source synthesizer still
        # sees the same story arrive from multiple sources, which is the whole
        # point of that feature.
        self._seen_rss_titles: set[str] = set()
        self._seen_reddit_titles: set[str] = set()
        self._event_pod = None  # set via set_event_pod() once EventPod exists
        self._long_term_desk = None  # set via set_long_term_desk() once LongTermDesk exists
        self._company_directory: list[tuple[str, str]] = []  # (lowercase full name, symbol)
        # Every symbol we'll actually accept a trade signal for — name-matching
        # against the directory can only ever produce a real symbol, but the LLM
        # fallback (_llm_extract_symbol) can hallucinate or pick up a non-tradeable
        # name (an exchange, a private company) mentioned in a headline. This set
        # is the gate that catches that before it ever reaches the trading pipeline.
        self._valid_symbols: set[str] = set(_COMPANY_ALIASES.values())

    def add_symbols(self, symbols: list[str]) -> None:
        for s in symbols:
            if s in self._watched_symbols:
                continue
            self._watched_symbols[s] = None
            if len(self._watched_symbols) > _MAX_WATCHED_SYMBOLS:
                oldest = next(iter(self._watched_symbols))
                del self._watched_symbols[oldest]

    def get_synthesis_feed(self) -> list[dict]:
        return list(self._synthesis_feed)

    def get_gist_feed(self) -> list[dict]:
        """Most actionable first — buy/avoid calls surfaced above neutral
        "watch" items, since those are worth a human's attention and watch
        items aren't. Recency is preserved within each tier since the
        underlying list is already newest-first and sorted() is stable."""
        priority = {"buy": 0, "avoid": 0, "watch": 1}
        return sorted(self._gist_feed, key=lambda g: priority.get(g.get("stance"), 1))

    def remove_symbol(self, symbol: str) -> None:
        self._watched_symbols.pop(symbol, None)

    def set_event_pod(self, pod) -> None:
        """Wire in EventPod so every fetched headline can be evaluated for a trade,
        independent of the severity check used for Guardian risk alerts."""
        self._event_pod = pod

    def set_long_term_desk(self, desk) -> None:
        """Wire in LongTermDesk so a headline's sentiment becomes a debated idea
        in Room 1 — the same committee that scores technical/fundamental signals."""
        self._long_term_desk = desk

    async def start(self) -> None:
        # Runs in the background — a slow/failed 26MB download shouldn't stall
        # boot, and _extract_symbol() falls back to the static alias list anyway.
        asyncio.create_task(self._load_company_directory(), name="news_watchdog_directory")
        self._task        = asyncio.create_task(self._poll_loop(), name="news_watchdog")
        self._rss_task    = asyncio.create_task(self._rss_loop(), name="news_watchdog_rss")
        self._reddit_task = asyncio.create_task(self._reddit_loop(), name="news_watchdog_reddit")
        self._synthesis_task = asyncio.create_task(self._synthesis_loop(), name="news_watchdog_synthesis")
        log.info("news_watchdog.started")

    async def _load_company_directory(self) -> None:
        """Build a full company-name → symbol map from the public NSE equity
        listing, so news about any listed company — small-cap included, not
        just our ~30-stock trading universe — can be matched to a real symbol."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_SCRIP_MASTER_URL, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    text = await resp.text()
            reader = csv.DictReader(io.StringIO(text))
            directory: list[tuple[str, str]] = []
            for row in reader:
                if row.get("Exch") != "N" or row.get("Series") != "EQ":
                    continue
                symbol    = (row.get("Name") or "").strip().upper()
                full_name = (row.get("FullName") or "").strip().lower()
                if symbol and len(full_name) > 3:
                    directory.append((full_name, symbol))
            directory.sort(key=lambda kv: -len(kv[0]))
            self._company_directory = directory
            self._valid_symbols.update(symbol for _, symbol in directory)
            log.info("news_watchdog.company_directory_loaded", count=len(directory))
        except Exception as exc:
            log.error("news_watchdog.company_directory_failed", error=repr(exc))
            self._company_directory = []

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        if self._rss_task and not self._rss_task.done():
            self._rss_task.cancel()
        if self._reddit_task and not self._reddit_task.done():
            self._reddit_task.cancel()
        if self._synthesis_task and not self._synthesis_task.done():
            self._synthesis_task.cancel()

    async def _poll_loop(self) -> None:
        while True:
            try:
                if feature_toggles.is_enabled("news"):
                    await self._check_news()
            except Exception as exc:
                log.error("news_watchdog.error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)

    async def _rss_loop(self) -> None:
        while True:
            try:
                if feature_toggles.is_enabled("news"):
                    await self._check_rss_feeds()
            except Exception as exc:
                log.error("news_watchdog.rss_error", error=str(exc))
            await asyncio.sleep(_RSS_POLL_INTERVAL)

    async def _reddit_loop(self) -> None:
        while True:
            try:
                if feature_toggles.is_enabled("reddit"):
                    await self._check_reddit()
            except Exception as exc:
                log.error("news_watchdog.reddit_error", error=str(exc))
            await asyncio.sleep(_REDDIT_POLL_INTERVAL)

    def _extract_symbol(self, text: str) -> Optional[str]:
        """Match a headline against known company names — full NSE directory
        first (covers small/mid-caps too), falling back to the hand-built
        blue-chip alias list if the directory failed to load."""
        lowered = text.lower()
        for full_name, symbol in self._company_directory:
            if full_name in lowered:
                return symbol
        for alias, symbol in _SORTED_ALIASES:
            if alias in lowered:
                return symbol
        return None

    async def _llm_extract_symbol(self, text: str) -> Optional[str]:
        """Last resort when name-matching finds nothing — ask the LLM to read
        the headline and identify the company itself (handles abbreviations,
        nicknames, or names not in the NSE directory verbatim)."""
        try:
            llm = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="guardian.news_watchdog.symbol_extract",
                system_prompt=_SYMBOL_EXTRACT_PROMPT,
                user_prompt=text[:500],
                tier=LLMTier.FAST,
            )
            symbol = result.get("symbol")
            symbol = symbol.strip().upper() if symbol else None
            # The LLM can hallucinate or pick up a name that isn't an actual
            # tradeable NSE equity (an exchange name, a private company) — only
            # accept it if it's a real, known symbol. Caught real bugs: "NSE"
            # and "CRED" both got traded at a fake ₹100 fallback price before this.
            if symbol and symbol not in self._valid_symbols:
                log.warning("news_watchdog.llm_symbol_rejected", symbol=symbol,
                            reason="not a recognised NSE-listed equity")
                return None
            return symbol
        except Exception as exc:
            log.debug("news_watchdog.llm_extract_failed", error=str(exc))
            return None

    async def _feed_long_term_desk(self, symbol: str, analysis: dict, headline: str) -> None:
        """Turn an analysed headline into a Room 1 idea — same debate pipeline
        the technical/fundamental strategies feed, not a side channel."""
        if self._long_term_desk is None or symbol == "MARKET":
            return
        sentiment = analysis.get("sentiment", "neutral")
        if sentiment == "neutral":
            return
        direction  = SignalDirection.LONG if sentiment == "positive" else SignalDirection.SHORT
        conviction = {"EMERGENCY": 0.8, "WARNING": 0.65}.get(analysis.get("severity", "INFO"), 0.55)
        await self._long_term_desk.ingest_news_signal(
            symbol=symbol, direction=direction, conviction=conviction,
            rationale=f"News: {headline[:150]}",
        )

    async def _check_rss_feeds(self) -> None:
        articles = await fetch_all_feeds(limit_per_feed=5)
        for article in articles:
            link  = article.get("link", "")
            title = article.get("title", "")
            if not link:
                continue
            norm = _normalize_headline(title)
            if link in self._seen_rss or (norm and norm in self._seen_rss_titles):
                continue
            self._seen_rss.add(link)
            if norm:
                self._seen_rss_titles.add(norm)

            content = article.get("summary", "")
            source  = article.get("source", "rss")
            text    = f"{title}\n{content}"
            symbol  = self._extract_symbol(text) or await self._llm_extract_symbol(text) or "MARKET"
            self._record_item(symbol, f"rss:{source}", title)
            if symbol != "MARKET":
                self.add_symbols([symbol])

            analysis = await self._analyse_news(symbol, text)
            await self._handle_analysis(symbol, analysis, f"[{source}] {title}", f"rss:{source}")
            await self._feed_long_term_desk(symbol, analysis, f"[{source}] {title}")

            if symbol != "MARKET" and self._event_pod is not None:
                await self._event_pod.trigger_event(symbol, "NSE", text)

    async def _check_reddit(self) -> None:
        """No-ops cleanly if REDDIT_CLIENT_ID/SECRET aren't in .env yet —
        fetch_subreddit_posts() just returns [] and logs once."""
        loop = asyncio.get_event_loop()
        posts = await loop.run_in_executor(None, fetch_subreddit_posts)

        for post in posts:
            url   = post.get("url", "")
            title = post.get("title", "")
            if not url:
                continue
            norm = _normalize_headline(title)
            if url in self._seen_reddit or (norm and norm in self._seen_reddit_titles):
                continue
            self._seen_reddit.add(url)
            if norm:
                self._seen_reddit_titles.add(norm)

            text  = f"{title}\n{post.get('selftext', '')}"
            symbol = self._extract_symbol(text) or await self._llm_extract_symbol(text) or "MARKET"
            self._record_item(symbol, "reddit", title)
            if symbol != "MARKET":
                self.add_symbols([symbol])

            analysis = await self._analyse_news(symbol, text)
            headline = f"[r/{post.get('subreddit', 'reddit')}] {title}"
            await self._handle_analysis(symbol, analysis, headline, "reddit")
            await self._feed_long_term_desk(symbol, analysis, headline)

            if symbol != "MARKET" and self._event_pod is not None:
                await self._event_pod.trigger_event(symbol, "NSE", text)

    async def _check_news(self) -> None:
        if not self._watched_symbols:
            return

        for symbol in list(self._watched_symbols):
            articles = await self._fetch_news(symbol)
            for article in articles:
                title = article.get("title", "")
                article_id = f"{symbol}:{title}"
                if not title or article_id in self._seen_articles:
                    continue
                self._seen_articles.add(article_id)
                self._record_item(symbol, "yahoo_finance", title)

                content = article.get("summary", "")
                analysis = await self._analyse_news(symbol, f"{title}\n{content}")
                await self._handle_analysis(symbol, analysis, title, "yahoo_finance")
                await self._feed_long_term_desk(symbol, analysis, title)

                if self._event_pod is not None:
                    await self._event_pod.trigger_event(symbol, "NSE", f"{title}\n{content}")

    async def _fetch_news(self, symbol: str) -> list[dict]:
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: get_news(symbol, limit=5))
        except Exception as exc:
            log.debug("news_watchdog.fetch_error", symbol=symbol, error=str(exc))
            return []

    def _record_item(self, symbol: str, source: str, headline: str) -> None:
        """Buffer one news item for cross-source overlap checking. Skipped for
        unresolved "MARKET"-wide headlines since there's no single symbol to
        cross-reference against the other pipelines."""
        if symbol == "MARKET":
            return
        cutoff = time.time() - _SYNTHESIS_WINDOW_S
        items = [it for it in self._recent_items.get(symbol, []) if it["ts"] >= cutoff]
        items.append({"source": source, "headline": headline, "ts": time.time()})
        self._recent_items[symbol] = items

    def _record_gist(self, symbol: str, source: str, analysis: dict, headline: str) -> None:
        """One gist per article, no waiting for another source to corroborate —
        every stock-specific headline gets its own buy/avoid/watch call."""
        if symbol == "MARKET":
            return
        self._gist_feed.insert(0, {
            "symbol":    symbol,
            "source":    source,
            "headline":  headline,
            "stance":    analysis.get("stance", "watch"),
            "sentiment": analysis.get("sentiment", "neutral"),
            "rationale": analysis.get("rationale", ""),
            "ts":        datetime.utcnow().isoformat(),
        })
        self._gist_feed = self._gist_feed[:30]

    async def _synthesis_loop(self) -> None:
        while True:
            try:
                if feature_toggles.is_enabled("news_extractor"):
                    await self._synthesize()
            except Exception as exc:
                log.error("news_watchdog.synthesize_error", error=str(exc))
            await asyncio.sleep(_SYNTHESIS_POLL_INTERVAL)

    async def _synthesize(self) -> None:
        """For every symbol that 2+ independent pipelines (Yahoo Finance/RSS/Reddit)
        reported on within the same window, ask the LLM once for a single
        consolidated view instead of leaving three disconnected analyses. Purely
        informational right now — feeds the Flow page only, does not replace or
        suppress the existing per-pipeline signals into EventPod / Long-Term Desk."""
        cutoff = time.time() - _SYNTHESIS_WINDOW_S
        for symbol, raw_items in list(self._recent_items.items()):
            items = [it for it in raw_items if it["ts"] >= cutoff]
            if not items:
                self._recent_items.pop(symbol, None)
                continue
            self._recent_items[symbol] = items

            sources = {it["source"].split(":")[0] for it in items}
            if len(sources) < 2:
                continue  # only one pipeline has seen this symbol so far — nothing to cross-check yet

            headlines_block = "\n".join(f"- [{it['source']}] {it['headline']}" for it in items[-8:])
            try:
                llm = LLMGateway.get()
                result = await llm.complete_json(
                    agent_id="guardian.news_extractor",
                    system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
                    user_prompt=f"Symbol: {symbol}\nHeadlines from {len(sources)} independent sources:\n{headlines_block}",
                )
            except Exception as exc:
                log.debug("news_watchdog.synthesize_llm_error", symbol=symbol, error=str(exc))
                continue

            self._synthesis_feed.insert(0, {
                "symbol":         symbol,
                "sources":        sorted(sources),
                "summary":        result.get("summary", ""),
                "recommendation": result.get("recommendation", "hold"),
                "rationale":      result.get("rationale", ""),
                "ts":             datetime.utcnow().isoformat(),
            })
            self._synthesis_feed = self._synthesis_feed[:20]
            self._recent_items.pop(symbol, None)  # this cluster is resolved — start fresh for the next one

    async def _analyse_news(self, symbol: str, text: str) -> dict:
        try:
            llm = LLMGateway.get()
            return await llm.complete_json(
                agent_id="guardian.news_watchdog",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=f"Symbol: {symbol}\nNews: {text[:2000]}",
            )
        except Exception:
            return {"severity": "INFO", "sentiment": "neutral",
                    "recommended_action": "hold", "rationale": "LLM unavailable"}

    async def _handle_analysis(self, symbol: str, analysis: dict, headline: str, source: str) -> None:
        self._record_gist(symbol, source, analysis, headline)

        severity = analysis.get("severity", "INFO")
        mode_map = {"WARNING": GuardianResponseMode.ALERT,
                    "EMERGENCY": GuardianResponseMode.LIQUIDATE}
        mode = mode_map.get(severity, GuardianResponseMode.ALERT)

        if severity == "INFO":
            return  # Don't flood bus with routine news

        alert = GuardianAlert(
            mode=mode,
            symbol=symbol,
            severity=severity.lower(),
            reason=f"News alert: {headline[:100]}",
            recommended_action=analysis.get("recommended_action", "hold"),
        )
        log.warning(
            "news_watchdog.alert",
            symbol=symbol,
            severity=severity,
            headline=headline[:80],
        )
        await self._bus.publish(
            Message(
                type=MessageType.GUARDIAN_ALERT,
                source="news_watchdog",
                payload=alert.model_dump(mode="json"),
            )
        )
