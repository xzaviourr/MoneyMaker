"""
Azure OpenAI provider implementation.
Handles chat completions (all non-embedding tiers) and embeddings.
"""
from __future__ import annotations

import json
import time
from typing import Any

import structlog
from openai import AsyncAzureOpenAI

from ...shared.config import settings
from ...shared.schemas import LLMRequest, LLMResponse, LLMTier
from ..base_provider import BaseLLMProvider
from ..tiers import TIER_CONFIGS
from .deployment_map import get_deployment

log = structlog.get_logger(__name__)

# o1 / o1-mini do not support system messages or temperature != 1.0
_O1_TIERS = {LLMTier.REASONING, LLMTier.DEEP}


class AzureOpenAIProvider(BaseLLMProvider):
    name = "azure_openai"

    def __init__(self) -> None:
        self._client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )

    def supports_tier(self, tier: LLMTier) -> bool:
        return True

    async def complete(self, request: LLMRequest) -> LLMResponse:
        cfg = TIER_CONFIGS[request.tier]
        deployment = get_deployment(request.tier)
        start = time.monotonic()

        try:
            if request.tier in _O1_TIERS:
                # o1 models: merge system into user message, no temperature param
                messages = [
                    {"role": "user",
                     "content": f"{request.system_prompt}\n\n{request.user_prompt}"}
                ]
                kwargs: dict[str, Any] = {
                    "model": deployment,
                    "messages": messages,
                    "max_completion_tokens": request.max_tokens,
                }
            else:
                messages = [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user",   "content": request.user_prompt},
                ]
                kwargs = {
                    "model": deployment,
                    "messages": messages,
                    "max_tokens": request.max_tokens,
                    "temperature": request.temperature,
                }
                if request.json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

            response = await self._client.chat.completions.create(**kwargs)

        except Exception as exc:
            log.error("llm.completion_error", agent=request.agent_id, error=str(exc))
            raise

        latency_ms = (time.monotonic() - start) * 1000
        usage = response.usage
        p_tokens = usage.prompt_tokens if usage else 0
        c_tokens = usage.completion_tokens if usage else 0

        cost = (
            p_tokens / 1000 * cfg.cost_per_1k_input
            + c_tokens / 1000 * cfg.cost_per_1k_output
        )
        content = response.choices[0].message.content or ""

        log.debug(
            "llm.completion",
            agent=request.agent_id,
            tier=request.tier.value,
            p_tokens=p_tokens,
            c_tokens=c_tokens,
            latency_ms=round(latency_ms, 1),
            cost_usd=round(cost, 6),
        )

        return LLMResponse(
            agent_id=request.agent_id,
            tier=request.tier,
            content=content,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            model_id=deployment,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        deployment = get_deployment(LLMTier.EMBEDDING)
        response = await self._client.embeddings.create(
            model=deployment,
            input=texts,
        )
        return [item.embedding for item in response.data]
