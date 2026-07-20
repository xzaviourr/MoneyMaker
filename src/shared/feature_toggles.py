"""
Manual on/off switches for individual data feeds / processing loops — lets the
user pause any node from the Flow page without restarting the whole system.
In-memory only; resets to all-on on restart.

Yahoo Finance quotes are pausable too: paper_broker.get_quote() already
refuses to place a new order when there's no real price (it rejects rather
than fabricating a ₹100 placeholder fill), so pausing just freezes existing
positions' mark-to-market at their last known price instead of corrupting
anything.

The remaining seven keys (five_paisa, database, data_sentinel, llm_gateway,
capital_tracker, pod_supervisor, paper_broker) exist purely so every node on
the Flow page has a working pause/resume button, per explicit request — none
of these gate any actual code path. They're not background loops (database
is just storage; llm_gateway/capital_tracker/paper_broker are called
synchronously by whoever needs them; data_sentinel/pod_supervisor only react
to bus events), so there's no periodic work to skip, and wiring a real halt
into the live order-execution path (paper_broker) or the circuit breaker's
pod_supervisor reaction risked turning a UI nicety into a safety bug. Toggle
freely — it's a flag, not a kill switch.
"""
from __future__ import annotations

from .config import toml_cfg

_DEFAULTS: dict[str, bool] = {
    "long_term_desk":     True,
    "news":               True,
    "reddit":             True,
    "yahoo_finance":      True,
    "regime_classifier":  True,
    "portfolio_guardian": True,
    "feedback":           True,
    "news_extractor":     True,
    "five_paisa":         True,
    "database":           True,
    "data_sentinel":      True,
    "llm_gateway":        True,
    "capital_tracker":    True,
    "pod_supervisor":     True,
    "paper_broker":       True,
}

# A portfolio can start with some of these off — e.g. [features] reddit =
# false to build a "no social sentiment" persona — via its own config.toml
# (MM_CONFIG_PATH). Still flips freely at runtime after startup through the
# Flow page, same as before; this only changes where each starts.
_TOGGLES: dict[str, bool] = {
    **_DEFAULTS,
    **toml_cfg.get("features", {}),
}


def is_enabled(name: str) -> bool:
    return _TOGGLES.get(name, True)


def set_enabled(name: str, enabled: bool) -> None:
    if name not in _TOGGLES:
        raise ValueError(f"Unknown toggle: {name}. Valid: {list(_TOGGLES)}")
    _TOGGLES[name] = enabled


def get_all() -> dict[str, bool]:
    return dict(_TOGGLES)
