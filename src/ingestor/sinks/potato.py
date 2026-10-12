"""potato-mesh sink: maps bridge events to potato-mesh API calls."""

import asyncio
import time

import httpx
import structlog

from ..config import settings
from ..events import ChatMessage, Event, NodeAdvert, PacketSeen, RepeaterStatus
from ..state import state
from .base import Sink

log = structlog.get_logger()

HEARTBEAT_INTERVAL = 60       # seconds
HEARTBEAT_INITIAL_DELAY = 15  # wait for first status before first heartbeat

_start_time = int(time.time())


class PotatoSink(Sink):
    name = "potato"

    def __init__(self) -> None:
        if not settings.potato_host or not settings.potato_api_token:
            raise ValueError("POTATO_HOST and POTATO_API_TOKEN are required when SINKS includes 'potato'")
        self._client = httpx.AsyncClient(
            base_url=settings.potato_host,
            headers={
                "Authorization": f"Bearer {settings.potato_api_token}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        self._packets_since_heartbeat = 0

    async def handle(self, event: Event) -> None:
        if isinstance(event, PacketSeen):
            self._packets_since_heartbeat += 1
        elif isinstance(event, RepeaterStatus):
            await self._status(event)
        elif isinstance(event, NodeAdvert):
            await self._advert(event)
        elif isinstance(event, ChatMessage):
            await self._message(event)

    async def run(self) -> None:
        await asyncio.sleep(HEARTBEAT_INITIAL_DELAY)
        while True:
            try:
                await self._heartbeat()
            except Exception:
                log.exception("heartbeat_failed")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, endpoint: str, payload: dict | list) -> None:
        try:
            r = await self._client.post(endpoint, json=payload)
            r.raise_for_status()
            log.debug("potato_api_ok", endpoint=endpoint, status=r.status_code)
        except httpx.HTTPStatusError as e:
            log.warning("potato_api_error", endpoint=endpoint, status=e.response.status_code,
                        body=e.response.text[:200])
        except httpx.RequestError as e:
            log.error("potato_request_failed", endpoint=endpoint, error=str(e))

    @staticmethod
    def _tag(data: dict) -> dict:
        return {**data, "protocol": "meshcore", "ingestor": settings.potato_ingestor_name}

    async def _send_node(self, node_id: str, data: dict) -> None:
        await self._post("/api/nodes", {node_id: self._tag(data)})

    async def _status(self, e: RepeaterStatus) -> None:
        await self._send_node(e.node_id, {
            "user": {"role": "REPEATER", "longName": e.name, "shortName": e.short_name},
            "deviceMetrics": {"voltage": e.voltage, "uptimeSeconds": e.uptime_secs},
            "lastHeard": e.timestamp,
            "lora_freq": e.lora_freq,
            "modem_preset": e.modem_preset,
        })

        if e.voltage is not None:
            await self._post("/api/telemetry", self._tag({
                "from_id": e.node_id,
                "rx_time": e.timestamp,
                "decoded": {
                    "portnum": "TELEMETRY_APP",
                    "telemetry": {
                        "time": e.timestamp,
                        "deviceMetrics": {"voltage": e.voltage, "uptimeSeconds": e.uptime_secs},
                    },
                },
            }))

    async def _advert(self, e: NodeAdvert) -> None:
        node_data: dict = {
            "user": {"role": e.role, "longName": e.name, "shortName": e.short_name,
                     "publicKey": e.public_key},
            "lastHeard": e.rx_time,
            "snr": e.snr,
            "rssi": e.rssi,
            "lora_freq": e.lora_freq,
            "modem_preset": e.modem_preset,
        }

        if e.latitude is not None and e.longitude is not None:
            node_data["position"] = {"latitude": e.latitude, "longitude": e.longitude}
            await self._post("/api/positions", self._tag({
                "from_id": e.node_id,
                "rx_time": e.rx_time,
                "latitude": e.latitude,
                "longitude": e.longitude,
                "snr": e.snr,
                "rssi": e.rssi,
            }))

        await self._send_node(e.node_id, node_data)

    async def _message(self, e: ChatMessage) -> None:
        await self._post("/api/messages", [self._tag({
            "id": e.msg_id,
            "rx_time": e.rx_time,
            "from_id": e.from_id,
            "to_id": "^all",
            "channel": 0,
            "channel_name": e.channel_name,
            "text": e.full_text,
            "portnum": "TEXT_MESSAGE_APP",
            "snr": e.snr,
            "rssi": e.rssi,
            "lora_freq": e.lora_freq,
            "modem_preset": e.modem_preset,
        })])

    async def _heartbeat(self) -> None:
        node_id = state.repeater_node_id or settings.repeater_node_id or None
        if not node_id:
            log.debug("heartbeat_skipped_no_node_id")
            return
        packets = self._packets_since_heartbeat
        self._packets_since_heartbeat = 0
        await self._post("/api/ingestors", {
            "node_id": node_id,
            "version": "1.0.0",
            "start_time": _start_time,
            "last_seen_time": int(time.time()),
            "protocol": "meshcore",
            "packets": packets,
            "lora_freq": state.lora_freq,
            "modem_preset": state.modem_preset,
        })
