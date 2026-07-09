"""
LLMGateway — the only LLM import used by agents.
Maps agent_id → tier → deployment automatically.
Logs every call to UsageTracker.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import structlog

from ..shared.schemas import LLMRequest, LLMResponse, LLMTier, LLMUsageRecord
from .base_provider import BaseLLMProvider
from .tiers import get_tier, TIER_CONFIGS
from .usage_tracker import UsageTracker

log = structlog.get_logger(__name__)

_MAX_CONCURRENT = 5  # max simultaneous Azure calls to avoid timeouts


class LLMGateway:
    _instance: Optional["LLMGateway"] = None

    def __init__(self, provider: BaseLLMProvider) -> None:
        self._provider = provider
        self._tracker = UsageTracker.get()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    @classmethod
    def get(cls) -> "LLMGateway":
        if cls._instance is None:
            raise RuntimeError("LLMGateway not initialised. Call LLMGateway.init() at startup.")
        return cls._instance

    @classmethod
    def init(cls, provider: Optional[BaseLLMProvider] = None) -> "LLMGateway":
        if provider is None:
            from .azure_openai.provider import AzureOpenAIProvider
            provider = AzureOpenAIProvider()
        cls._instance = cls(provider)
        return cls._instance

    # ── Public API ─────────────────────────────────────────────────────────

    async def complete(
        self,
        agent_id: str,
        system_prompt: str,
        user_prompt: str,
        tier: Optional[LLMTier] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_mode: bool = False,
    ) -> str:
        """
        Returns the raw text content of the completion.
        Tier is resolved from the agent_id map if not provided explicitly.
        """
        resolved_tier = tier or get_tier(agent_id)
        cfg = TIER_CONFIGS[resolved_tier]

        request = LLMRequest(
            agent_id=agent_id,
            tier=resolved_tier,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens or cfg.max_tokens,
            temperature=temperature if temperature is not None else cfg.temperature,
            json_mode=json_mode,
        )
        async with self._semaphore:
            response = await self._provider.complete(request)
        await self._record(response)
        return response.content

    async def complete_json(
        self,
        agent_id: str,
        system_prompt: str,
        user_prompt: str,
        tier: Optional[LLMTier] = None,
    ) -> dict:
        """Convenience wrapper that parses JSON from the response."""
        raw = await self.complete(
            agent_id=agent_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tier=tier,
            json_mode=True,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Extract JSON block if wrapped in markdown
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
            log.error("llm.json_parse_failed", agent=agent_id, raw=raw[:500])
            return {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._provider.embed(texts)

    # ── Internals ──────────────────────────────────────────────────────────

    async def _record(self, response: LLMResponse) -> None:
        rec = LLMUsageRecord(
            agent_id=response.agent_id,
            tier=response.tier,
            model_id=response.model_id,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.prompt_tokens + response.completion_tokens,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )
        await self._tracker.record(rec)

    @property
    def today_cost_usd(self) -> float:
        return self._tracker.today_cost()
