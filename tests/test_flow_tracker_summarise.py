"""Tests for FlowTracker's _summarise helper — pure function, no I/O."""
from src.shared.flow_tracker import _summarise


def test_summarise_regime():
    s = _summarise({"trend": "TRENDING", "risk_posture": "RISK_ON", "vix": 14.5})
    assert "TRENDING" in s
    assert "14.5" in s


def test_summarise_signal():
    s = _summarise({"direction": "long", "symbol": "RELIANCE", "confidence": 0.8})
    assert "RELIANCE" in s
    assert "long" in s


def test_summarise_quote():
    s = _summarise({"symbol": "TCS", "ltp": 3456.78})
    assert "TCS" in s
    assert "3456.78" in s


def test_summarise_empty():
    assert _summarise(None) == ""
    assert _summarise({}) == ""
