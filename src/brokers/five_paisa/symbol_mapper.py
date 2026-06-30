"""
Maps standard NSE/BSE ticker strings ↔ 5Paisa scrip codes.
Scrip code master is fetched once at startup and cached.
"""
from __future__ import annotations

import asyncio
import csv
import io
from typing import Optional

import aiohttp
import structlog

log = structlog.get_logger(__name__)

# 5Paisa scrip master URL (NSE equity)
_SCRIP_MASTER_URL = (
    "https://images.5paisa.com/website/scripmaster-csv-format.csv"
)

# Exchange code mapping
EXCHANGE_MAP: dict[str, str] = {
    "NSE": "N",
    "BSE": "B",
    "NFO": "D",
    "MCX": "M",
}
_EXCHANGE_MAP_REVERSE: dict[str, str] = {v: k for k, v in EXCHANGE_MAP.items()}

# Order type mapping
ORDER_TYPE_MAP: dict[str, str] = {
    "market":    "M",
    "limit":     "L",
    "stop_loss": "SL",
    "sl_market": "SLM",
}


class SymbolMapper:
    _instance: Optional["SymbolMapper"] = None

    def __init__(self) -> None:
        self._code_to_symbol: dict[int, str] = {}
        self._symbol_to_code: dict[str, int] = {}   # "RELIANCE_NSE" → 3045
        self._loaded = False
        self._lock = asyncio.Lock()

    @classmethod
    def get(cls) -> "SymbolMapper":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            await self._load_master()

    async def _load_master(self) -> None:
        log.info("symbol_mapper.loading_master")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_SCRIP_MASTER_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    text = await resp.text()
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                try:
                    if row.get("ExchType") != "C":
                        continue
                    code = int(row.get("Scripcode") or 0)
                    symbol = (row.get("Name") or "").strip().upper()
                    exch_letter = (row.get("Exch") or "").strip().upper()
                    exch = _EXCHANGE_MAP_REVERSE.get(exch_letter)
                    if code and symbol and exch:
                        key = f"{symbol}_{exch}"
                        self._symbol_to_code[key] = code
                        self._code_to_symbol[code] = symbol
                except (ValueError, KeyError):
                    continue
            self._loaded = True
            log.info("symbol_mapper.loaded", count=len(self._symbol_to_code))
        except Exception as exc:
            log.error("symbol_mapper.load_failed", error=str(exc))
            self._loaded = True  # proceed anyway; use fallback

    def get_scrip_code(self, symbol: str, exchange: str) -> int:
        """Returns scrip code or 0 if not found."""
        key = f"{symbol.upper()}_{exchange.upper()}"
        return self._symbol_to_code.get(key, 0)

    def get_symbol(self, scrip_code: int) -> Optional[str]:
        return self._code_to_symbol.get(scrip_code)

    def exchange_code(self, exchange: str) -> str:
        return EXCHANGE_MAP.get(exchange.upper(), "N")

    def order_type_code(self, order_type: str) -> str:
        return ORDER_TYPE_MAP.get(order_type.lower(), "L")
