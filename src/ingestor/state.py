from dataclasses import dataclass


@dataclass
class BridgeState:
    """Repeater facts learned from `status` messages, shared by core and sinks."""

    repeater_node_id: str | None = None
    lora_freq: float | None = None
    modem_preset: str | None = None


state = BridgeState()
