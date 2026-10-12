"""Normalized events produced by the core and consumed by sinks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PacketSeen:
    """Any packet received on the `packets` subtopic (before decoding)."""


@dataclass(frozen=True)
class RepeaterStatus:
    node_id: str
    name: str
    short_name: str
    voltage: float | None
    uptime_secs: int | None
    lora_freq: float | None
    modem_preset: str | None
    timestamp: int


@dataclass(frozen=True)
class NodeAdvert:
    node_id: str
    role: str
    name: str
    short_name: str
    public_key: str
    rx_time: int
    snr: float
    rssi: int
    lora_freq: float | None
    modem_preset: str | None
    latitude: float | None = None
    longitude: float | None = None


def format_chat_text(sender: str, text: str) -> str:
    """MeshCore wire format 'Sender: body' (potato-mesh parses the sender from it)."""
    return f"{sender}: {text}" if sender and text else text


@dataclass(frozen=True)
class ChatMessage:
    msg_id: int
    rx_time: int
    from_id: str
    sender: str
    text: str
    channel_name: str
    channel_hash: str
    snr: float
    rssi: int
    lora_freq: float | None
    modem_preset: str | None
    repeater: str = ""  # name of the repeater that heard the message, "" if unknown

    @property
    def full_text(self) -> str:
        return format_chat_text(self.sender, self.text)


Event = PacketSeen | RepeaterStatus | NodeAdvert | ChatMessage
