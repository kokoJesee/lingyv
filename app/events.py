"""In-process synchronous event bus.

Modules publish facts ("canvas.confirmed") instead of importing each other.
M1 runs single-process; a broker can replace this without touching callers.
Handlers must stay fast and must not raise into the publisher.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]

_subscribers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(topic: str, handler: Handler) -> None:
    if handler not in _subscribers[topic]:
        _subscribers[topic].append(handler)


def unsubscribe(topic: str, handler: Handler) -> None:
    handlers = _subscribers.get(topic, [])
    if handler in handlers:
        handlers.remove(handler)


def publish(topic: str, payload: dict[str, Any]) -> None:
    for handler in list(_subscribers.get(topic, [])):
        try:
            handler(payload)
        except Exception:  # noqa: BLE001 - a broken listener must not break the writer
            logger.exception("event handler failed | topic=%s", topic)


def clear_subscribers() -> None:
    """Test helper: drop every subscription."""
    _subscribers.clear()
