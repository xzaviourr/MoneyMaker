"""Abstract LLM provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..shared.schemas import LLMRequest, LLMResponse, LLMTier


class BaseLLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Generate a chat completion."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for a list of texts."""

    @abstractmethod
    def supports_tier(self, tier: LLMTier) -> bool:
        """Return True if this provider handles the given tier."""
