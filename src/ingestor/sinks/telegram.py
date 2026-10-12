"""Telegram sink: forwards decrypted channel messages to a Telegram chat."""

import asyncio
import html
import re
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
import structlog

from ..config import settings
from ..events import ChatMessage, Event
from .base import Sink

log = structlog.get_logger()

MAX_TEXT_LEN = 700    # html.escape can expand text 5x ("&" -> "&amp;"); Telegram limit is 4096
DEDUP_CACHE_SIZE = 1000
MAX_RETRY_AFTER = 30  # seconds; don't stall the sink longer than this on HTTP 429


_PLACEHOLDER = re.compile(r"\{(\w+)\}")
FIELDS = ("channel", "sender", "text", "repeater", "snr", "rssi", "freq", "preset", "time", "from_id")


def _parse_template(template: str) -> list[str]:
    template = template.replace("\\n", "\n")
    unknown = {f for f in _PLACEHOLDER.findall(template) if f not in FIELDS}
    if unknown:
        raise ValueError(
            f"Unknown placeholder(s) in TELEGRAM_FORMAT: {sorted(unknown)}; allowed: {list(FIELDS)}"
        )
    return template.split("\n")


def _channel_label(name: str) -> str:
    """'#news' for hashtag channels, plain 'Public' for the fixed Public channel."""
    if not name or name.lower() == "public":
        return name
    return f"#{name}"


def _fields(event: ChatMessage) -> dict[str, str]:
    values = {
        "channel": _channel_label(event.channel_name),
        "sender": event.sender,
        "text": event.text[:MAX_TEXT_LEN],
        "repeater": event.repeater,
        "snr": f"{event.snr:.1f}",
        "rssi": str(event.rssi),
        "freq": f"{event.lora_freq:.3f}" if event.lora_freq is not None else "",
        "preset": event.modem_preset or "",
        "time": datetime.fromtimestamp(event.rx_time, timezone.utc).strftime("%H:%M:%S UTC"),
        "from_id": event.from_id.removeprefix("!"),
    }
    return {k: html.escape(v) for k, v in values.items()}


def render(lines: list[str], event: ChatMessage) -> str:
    """Fill the template line by line. A line whose placeholders are all empty is
    dropped, separators left dangling by an empty field are trimmed, and literal
    blank lines are kept (collapsed, never leading or trailing)."""
    values = _fields(event)
    out: list[str] = []
    for line in lines:
        names = _PLACEHOLDER.findall(line)
        if not names:
            out.append(line)
            continue
        if not any(values[n] for n in names):
            continue
        rendered = _PLACEHOLDER.sub(lambda m: values[m.group(1)], line)
        if "text" not in names:  # never alter the message body itself
            rendered = _clean_separators(rendered)
        if rendered:
            out.append(rendered)

    result: list[str] = []
    for line in out:
        if line == "" and (not result or result[-1] == ""):
            continue
        result.append(line)
    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)


def _clean_separators(line: str) -> str:
    line = re.sub(r"(<[^>/]+>)\s*·\s*", r"\1", line)           # "<i> · x" -> "<i>x"
    line = re.sub(r"\s*·\s*(</[^>]+>)", r"\1", line)           # "x · </i>" -> "x</i>"
    line = re.sub(r"(?:\s·\s){2,}", " · ", line)               # "a ·  · b" -> "a · b"
    return line.strip(" ·\t").rstrip(" -")


def _channel_key(name: str) -> str:
    return name.strip().lstrip("#").lower()


class TelegramSink(Sink):
    name = "telegram"

    def __init__(self) -> None:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when SINKS includes 'telegram'"
            )
        # The token is part of the URL, so never log request URLs or raise_for_status() errors.
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{settings.telegram_bot_token}",
            timeout=10.0,
        )
        self._channels = {_channel_key(c) for c in settings.telegram_channels}
        self._template = _parse_template(settings.telegram_format)
        self._seen: OrderedDict[int, None] = OrderedDict()
        self._lock = asyncio.Lock()  # Telegram allows ~1 message/second per chat

    async def handle(self, event: Event) -> None:
        if not isinstance(event, ChatMessage):
            return
        # Undecrypted packets carry no text and are useless in a chat.
        if not event.text:
            return
        if self._channels and _channel_key(event.channel_name) not in self._channels:
            return
        if self._already_sent(event.msg_id):
            return
        await self._send(render(self._template, event))

    async def aclose(self) -> None:
        await self._client.aclose()

    def _already_sent(self, msg_id: int) -> bool:
        """The same physical message can arrive via several repeaters."""
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = None
        if len(self._seen) > DEDUP_CACHE_SIZE:
            self._seen.popitem(last=False)
        return False

    async def _send(self, text: str) -> None:
        payload = {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with self._lock:
            for attempt in range(2):
                try:
                    r = await self._client.post("/sendMessage", json=payload)
                except httpx.RequestError as e:
                    log.error("telegram_request_failed", error=type(e).__name__)
                    return

                if r.status_code == 429 and attempt == 0:
                    retry_after = _retry_after(r)
                    log.warning("telegram_rate_limited", retry_after=retry_after)
                    await asyncio.sleep(min(retry_after, MAX_RETRY_AFTER))
                    continue

                if r.status_code != 200:
                    log.warning("telegram_api_error", status=r.status_code, body=r.text[:200])
                else:
                    log.debug("telegram_sent")
                return


def _retry_after(r: httpx.Response) -> int:
    try:
        return int(r.json().get("parameters", {}).get("retry_after", 1))
    except (ValueError, AttributeError):
        return 1
