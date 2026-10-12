# meshcore-potato-ingestor

Reads MeshCore node data from an MQTT broker and forwards it to pluggable
sinks. Built-in sink: a [potato-mesh](https://github.com/l5yth/potato-mesh) map
instance. Sinks are enabled with `SINKS` (default `potato`).

**Why:** The existing potato-mesh MeshCore ingestor requires a direct companion
node connection. This ingestor works with the
[VBart/MeshCoreTel-firmware](https://github.com/VBart/MeshCoreTel-firmware)
fork, which publishes all received packets to MQTT — no companion node needed.

---

## How It Works

```
MeshCore Repeater (VBart firmware)
        │  MQTT publish  meshcore/{iata}/{device_id}/{status|packets|raw}
        ▼
   Mosquitto Broker  (included via Docker Compose)
        │  $share/ingestor-group/meshcore/#
        ▼
   Bridge core (this project)
   ├── status   → RepeaterStatus event
   ├── packets  → decoded with meshcoredecoder
   │   ├── Advert     → NodeAdvert event
   │   ├── GroupText  → ChatMessage event (public channels, decrypted)
   │   └── Path/Trace → discarded
   └── raw / unknown → discarded, retain cleared
        │  EventBus → every enabled sink
        ▼
   potato sink → potato-mesh API
```

Multiple ingestor replicas can run in parallel — MQTT shared subscriptions
ensure each message is processed by exactly one replica.

---

## Requirements

- Docker & Docker Compose v2
- A MeshCore repeater running VBart/MeshCoreTel-firmware with MQTT pointing at
  this broker
- A running [potato-mesh](https://github.com/l5yth/potato-mesh) instance with
  an API token

---

## Setup

### 1. Clone and configure

```bash
git clone <this-repo>
cd meshcore-observer-potato-mesh-ingestor
cp .env.example .env   # then edit .env
```

Key variables in `.env`:

| Variable | Description |
|---|---|
| `MQTT_USERNAME` / `MQTT_PASSWORD` | Broker credentials (must match passwd file) |
| `SINKS` | Comma-separated sinks to enable (default `potato`) |
| `POTATO_HOST` | potato sink: potato-mesh base URL, e.g. `https://map.example.com` |
| `POTATO_API_TOKEN` | potato sink: potato-mesh API bearer token |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | telegram sink: bot token from @BotFather and target chat id |
| `TELEGRAM_CHANNELS_RAW` | telegram sink: channel names to forward, e.g. `news,test`. Empty = all decrypted channels |
| `TELEGRAM_FORMAT` | telegram sink: message template, see [Telegram Sink](#telegram-sink) |
| `INGESTOR_NAME` | Name shown in potato-mesh ingestors list |
| `CHANNELS_RAW` | Comma-separated channel names to forward, e.g. `test,news`. Empty = all public channels |
| `REPEATER_NODE_ID` | Optional: repeater node id (e.g. `!3c15e67e`) for heartbeat before first status arrives |

### 2. Create Mosquitto password file

```bash
docker run --rm eclipse-mosquitto:2 \
  mosquitto_passwd -b /dev/stdout meshcore your-strong-password \
  > mosquitto/config/passwd
```

The username and password must match `MQTT_USERNAME` / `MQTT_PASSWORD` in `.env`.
The `passwd` file is gitignored — never commit it.

### 3. Start

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f ingestor
docker compose logs -f mosquitto
```

---

## Channel Configuration

GroupText messages are encrypted per-channel. Keys are **derived automatically**
from channel names — no manual key entry.

```env
# Forward only these channels (keys derived from names automatically)
CHANNELS_RAW=test,news

# Forward all public channels, decrypt where possible (default)
CHANNELS_RAW=
```

The `Public` channel key is always registered at startup. Only broadcast
messages (`to_id = "^all"`) are forwarded; DMs are always dropped.

---

## Scaling

Run multiple replicas to handle higher message volume:

```bash
docker compose up -d --scale ingestor=3
```

Or set permanently in `.env`:

```env
INGESTOR_REPLICAS=3
```

Each MQTT message is delivered to exactly one replica — no duplicate processing.

---

## MQTT Retain Cleanup

- After processing each retained message, the ingestor publishes an empty
  payload to the same topic → Mosquitto removes the retained copy immediately.
- A background task runs every hour and clears any retained messages whose
  `timestamp` is older than **6 hours** (safety net for messages missed during
  downtime).

---

## Project Structure

```
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── mosquitto/
│   └── config/
│       ├── mosquitto.conf
│       └── passwd.example
└── src/ingestor/
    ├── config.py          — settings (pydantic-settings, reads .env)
    ├── main.py            — event loop, MQTT reconnect, heartbeat, retain cleanup
    ├── processor.py       — message routing, GroupText decryption, retain clear
    ├── handlers.py        — decoded packets → bridge events
    ├── events.py          — event dataclasses
    ├── bus.py             — fan-out of events to sinks
    ├── state.py           — repeater id / LoRa params shared with sinks
    └── sinks/
        ├── base.py        — Sink interface
        ├── potato.py      — potato-mesh sink (API mapping + heartbeat)
        └── telegram.py    — Telegram sink (channel messages → chat)
```

---

## Telegram Sink

Forwards decrypted channel messages to a Telegram chat. Default message:

```
#news · ALEX KORO
Potato
KORO MW RPT · SNR 4.0 · RSSI -80
```

Enable with `SINKS=potato,telegram` (or `telegram`
alone).

1. Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
2. Add the bot to your group/channel (as admin for channels).
3. Get the chat id (e.g. `-1001234567890`) and set `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID` in `.env`.
4. Optionally limit channels with `TELEGRAM_CHANNELS_RAW=news,test`.

### Message template

`TELEGRAM_FORMAT` is a Telegram-HTML template (`<b>`, `<i>`, `<code>`, ...); write
`\n` for a line break. Placeholders (values are HTML-escaped automatically):

| Placeholder | Value |
|---|---|
| `{channel}` | channel name without `#` |
| `{sender}` | sender display name |
| `{text}` | message body |
| `{repeater}` | name of the repeater that heard it (empty until its first `status`) |
| `{snr}` / `{rssi}` | signal quality, e.g. `4.0` / `-80` |
| `{freq}` / `{preset}` | LoRa frequency in MHz / preset, e.g. `869.618` / `SF8/BW62.5/CR8` |
| `{time}` | reception time, UTC `HH:MM:SS UTC` |
| `{from_id}` | sender node id, e.g. `!aabbccdd` |

A line whose placeholders are all empty is dropped, and a separator left
dangling by an empty field is trimmed. An unknown placeholder fails fast at
startup. Example: `TELEGRAM_FORMAT={time} <b>{sender}</b>: {text}`.

Messages that could not be decrypted (unknown channel key) are never sent. The
same message heard via several repeaters is sent once per process; with
multiple ingestor replicas a message may be duplicated.

---

## Adding a Sink

1. Create `src/ingestor/sinks/<name>.py` with a `Sink` subclass: implement
   `handle(event)` (ignore event types you don't care about); optionally `run()`
   for a background task and `aclose()`.
2. Register it in `build_sinks()` in `src/ingestor/sinks/__init__.py`.
3. Enable it: `SINKS=potato,<name>`.

---

## Firmware Configuration

On the MeshCore repeater (VBart/MeshCoreTel-firmware), configure the MQTT
broker to point at the machine running this stack:

```
MQTT Host: <your-server-ip>
MQTT Port: 1883
MQTT Username: <MQTT_USERNAME from .env>
MQTT Password: <MQTT_PASSWORD from .env>
```

---

## Development

```bash
uv pip install -e .
python -m ingestor.main
```
