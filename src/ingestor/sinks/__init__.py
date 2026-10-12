from .base import Sink


def build_sinks(names: list[str]) -> list[Sink]:
    sinks: list[Sink] = []
    for name in names:
        if name == "potato":
            from .potato import PotatoSink

            sinks.append(PotatoSink())
        elif name == "telegram":
            from .telegram import TelegramSink

            sinks.append(TelegramSink())
        else:
            raise ValueError(f"Unknown sink {name!r}; available: potato, telegram")
    if not sinks:
        raise ValueError("No sinks enabled; set SINKS, e.g. SINKS=potato")
    return sinks


__all__ = ["Sink", "build_sinks"]
