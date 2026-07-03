"""Tests for MessageBus — pub/sub and backpressure."""
import asyncio
import pytest
from src.shared.message_bus import MessageBus
from src.shared.schemas import Message, MessageType


@pytest.fixture(autouse=True)
def reset_bus():
    MessageBus.reset()
    yield
    MessageBus.reset()


async def test_subscribe_and_receive():
    bus = MessageBus.get()
    await bus.start()
    received = []

    async def handler(msg: Message):
        received.append(msg)

    bus.subscribe(MessageType.REGIME_CHANGE, handler)
    msg = Message(type=MessageType.REGIME_CHANGE, source="test", payload={"trend": "TRENDING"})
    await bus.publish(msg)
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].source == "test"
    await bus.stop()


async def test_unsubscribe_stops_delivery():
    bus = MessageBus.get()
    await bus.start()
    received = []

    async def handler(msg: Message):
        received.append(msg)

    bus.subscribe(MessageType.REGIME_CHANGE, handler)
    bus.unsubscribe(MessageType.REGIME_CHANGE, handler)
    await bus.publish(Message(type=MessageType.REGIME_CHANGE, source="test", payload={}))
    await asyncio.sleep(0.05)
    assert len(received) == 0
    await bus.stop()


async def test_subscribe_all_receives_every_type():
    bus = MessageBus.get()
    await bus.start()
    received = []

    async def catch_all(msg: Message):
        received.append(msg.type)

    bus.subscribe_all(catch_all)
    await bus.publish(Message(type=MessageType.REGIME_CHANGE, source="s", payload={}))
    await bus.publish(Message(type=MessageType.GUARDIAN_ALERT, source="s", payload={}))
    await asyncio.sleep(0.05)
    assert MessageType.REGIME_CHANGE in received
    assert MessageType.GUARDIAN_ALERT in received
    await bus.stop()
