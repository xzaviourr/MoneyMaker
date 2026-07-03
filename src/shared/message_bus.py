"""
In-process async pub/sub message bus.
Replace _publish/_dispatch with Redis Streams for multi-process scale.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

import structlog

from .schemas import Message, MessageType

log = structlog.get_logger(__name__)

MessageHandler = Callable[[Message], Coroutine[Any, Any, None]]


class MessageBus:
    """Singleton async event bus.  Call MessageBus.get() everywhere."""

    _instance: MessageBus | None = None

    def __init__(self) -> None:
        self._subscribers: dict[MessageType, list[MessageHandler]] = defaultdict(list)
        self._wildcard: list[MessageHandler] = []
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=20_000)
        self._running = False
        self._dispatch_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._stats: dict[str, int] = defaultdict(int)

    # ── Singleton ──────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "MessageBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    # ── Subscription ───────────────────────────────────────────────────────

    def subscribe(self, msg_type: MessageType, handler: MessageHandler) -> None:
        self._subscribers[msg_type].append(handler)
        log.debug("bus.subscribed", type=msg_type.value, handler=handler.__qualname__)

    def subscribe_all(self, handler: MessageHandler) -> None:
        """Subscribe to every message type (useful for logging/audit)."""
        self._wildcard.append(handler)

    def unsubscribe(self, msg_type: MessageType, handler: MessageHandler) -> None:
        try:
            self._subscribers[msg_type].remove(handler)
        except ValueError:
            pass

    # ── Publishing ─────────────────────────────────────────────────────────

    async def publish(self, message: Message) -> None:
        qsize = self._queue.qsize()
        if qsize >= 16_000:  # 80 % of maxsize=20_000
            log.warning("bus.queue_high_watermark", qsize=qsize, type=message.type.value)
        try:
            await self._queue.put(message)
            self._stats[message.type.value] += 1
        except asyncio.QueueFull:
            log.error("bus.queue_full", type=message.type.value, qsize=self._queue.qsize())

    def publish_nowait(self, message: Message) -> None:
        """Non-blocking publish; drops message if queue is full."""
        try:
            self._queue.put_nowait(message)
            self._stats[message.type.value] += 1
        except asyncio.QueueFull:
            log.error("bus.queue_full", type=message.type.value)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(), name="message_bus_dispatch"
        )
        log.info("bus.started")

    async def stop(self) -> None:
        self._running = False
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        log.info("bus.stopped", stats=dict(self._stats))

    # ── Dispatch ───────────────────────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(message)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("bus.loop_error", error=str(exc), exc_info=True)

    async def _dispatch(self, message: Message) -> None:
        typed_handlers = self._subscribers.get(message.type, [])
        all_handlers = typed_handlers + self._wildcard

        if not all_handlers:
            return

        tasks = [asyncio.create_task(h(message)) for h in all_handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for handler, result in zip(all_handlers, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                log.error(
                    "bus.handler_error",
                    handler=handler.__qualname__,
                    type=message.type.value,
                    error=str(result),
                    exc_info=result,
                )

    # ── Helpers ────────────────────────────────────────────────────────────

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
