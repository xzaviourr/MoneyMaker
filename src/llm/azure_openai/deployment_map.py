"""Resolves LLMTier → Azure deployment name from config."""
from __future__ import annotations

from ...shared.config import toml_cfg
from ...shared.schemas import LLMTier

_DEFAULTS: dict[str, str] = {
    "fast":      "gpt-4o-mini",
    "standard":  "gpt-4o",
    "reasoning": "o1-mini",
    "deep":      "o1",
    "embedding": "text-embedding-3-large",
}


def get_deployment(tier: LLMTier) -> str:
    overrides = (
        toml_cfg
        .get("llm", {})
        .get("deployments", {})
    )
    return overrides.get(tier.value, _DEFAULTS[tier.value])
