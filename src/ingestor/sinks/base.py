from abc import ABC, abstractmethod

from ..events import Event


class Sink(ABC):
    """Destination for bridge events (potato-mesh, Telegram, ...)."""

    name: str

    @abstractmethod
    async def handle(self, event: Event) -> None:
        """Process one event. Unknown event types must be ignored."""

    async def run(self) -> None:
        """Optional long-running background task (e.g. heartbeat)."""

    async def aclose(self) -> None:
        """Release resources on shutdown."""
