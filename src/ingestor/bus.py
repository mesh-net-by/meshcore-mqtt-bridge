import asyncio

import structlog

from .events import Event
from .sinks import Sink

log = structlog.get_logger()


class EventBus:
    """Fans events out to all sinks; a failing sink never affects the others."""

    def __init__(self, sinks: list[Sink]) -> None:
        self.sinks = sinks

    async def publish(self, event: Event) -> None:
        results = await asyncio.gather(
            *(sink.handle(event) for sink in self.sinks), return_exceptions=True
        )
        for sink, result in zip(self.sinks, results):
            if isinstance(result, Exception):
                log.error("sink_failed", sink=sink.name, event_type=type(event).__name__, error=repr(result))

    async def aclose(self) -> None:
        await asyncio.gather(*(sink.aclose() for sink in self.sinks), return_exceptions=True)
