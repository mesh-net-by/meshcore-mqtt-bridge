# meshcore-mqtt-bridge

Reads MeshCore node data from an MQTT broker and forwards it to pluggable
sinks. Built-in sinks: **Telegram** (channel messages → chat, the recommended
default) and a [potato-mesh](https://github.com/l5yth/potato-mesh) map
instance. Sinks are enabled with `SINKS` (this README assumes `telegram`).

**Why:** The existing potato-mesh MeshCore ingestor requires a direct companion
node connection. This ingestor works with the
[VBart/MeshCoreTel-firmware](https://github.com/VBart/MeshCoreTel-firmware)
fork, which publishes all received packets to MQTT — no companion node needed.

---

## How It Works

```
┌──────────────────────────┐
│ MeshCore Repeater        │
│ (VBart firmware)         │
└────────────┬─────────────┘
             │ MQTT publish
             │ meshcore/{iata}/{device_id}/{status|packets|raw}
             ▼
┌──────────────────────────┐
│ Mosquitto broker         │
│ (Docker Compose)         │
└────────────┬─────────────┘
             │ shared subscription
             │ $share/ingestor-group/meshcore/#
             ▼
┌───────────────────────────────────────────────────────┐
│ Ingestor                                              │
│                                                       │
│  status  ──────────────────────► RepeaterStatus       │
│  packets ─► meshcoredecoder                           │
│              ├─ Advert     ────► NodeAdvert           │
│              ├─ GroupText  ────► ChatMessage          │
│              │   (key derived from channel name,      │
│              │    decrypted, filtered by CHANNELS_RAW)│
│              └─ Path/Trace/DM ─► dropped              │
│  raw / unknown ────────────────► dropped, retain      │
│                                  cleared              │
└────────────┬──────────────────────────────────────────┘
             │ EventBus (fan-out, a failing sink never
             │ affects the others)
      ┌──────┴────────────────┐
      ▼                       ▼
┌───────────────┐      ┌───────────────┐
│ Telegram sink │      │ Potato sink   │
│ (default)     │      │ (optional)    │
└──────┬────────┘      └──────┬────────┘
       ▼                      ▼
 Telegram Bot API      potato-mesh API
 (ChatMessage only)    (nodes, messages, positions,
                        telemetry, heartbeat)
```

Multiple ingestor replicas can run in parallel — MQTT shared subscriptions
ensure each message is processed by exactly one replica.

---

## Requirements

- Docker & Docker Compose v2
- A MeshCore repeater running VBart/MeshCoreTel-firmware with MQTT pointing at
  this broker
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and a target
  chat id (for the default `telegram` sink)
- *Optional:* a running [potato-mesh](https://github.com/l5yth/potato-mesh)
  instance with an API token (only if you enable the `potato` sink)

---

## Setup

### 1. Clone and configure

```bash
git clone <this-repo>
cd meshcore-mqtt-bridge
cp .env.example .env   # then edit .env
```

`.env.example` ships with `SINKS=telegram`. Note that without `SINKS` the code
falls back to `potato`.

Variables in `.env`. **Required** column: `yes` = always, `telegram` / `potato` =
only when that sink is in `SINKS`, `no` = optional.

| Variable | Required | Default | Description |
|---|---|---|---|
| `MQTT_USERNAME` | yes | — | Broker username (must match the Mosquitto passwd file) |
| `MQTT_PASSWORD` | yes | — | Broker password |
| `MQTT_HOST` | no | `localhost` | Broker address (in Docker Compose set `mosquitto`) |
| `MQTT_PORT` | no | `1883` | Broker port |
| `MQTT_TOPIC_PREFIX` | no | `meshcore` | Topic prefix |
| `MQTT_CONSUMER_GROUP` | no | `ingestor-group` | Shared subscription group |
| `SINKS` | yes | `potato` (code default) | Comma-separated sinks: `telegram`, `potato`. `.env.example` sets `telegram` |
| `TELEGRAM_BOT_TOKEN` | telegram | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | telegram | — | Target chat id, e.g. `-1001234567890` |
| `TELEGRAM_CHANNELS_RAW` | no | empty | Channel names to forward to Telegram. Empty = all decrypted channels |
| `TELEGRAM_FORMAT` | no | built-in template | Message template, see [Telegram Sink](#telegram-sink) |
| `POTATO_HOST` | potato | — | potato-mesh base URL, e.g. `https://map.example.com` |
| `POTATO_API_TOKEN` | potato | — | potato-mesh API bearer token |
| `POTATO_INGESTOR_NAME` | potato | `mqtt-ingestor` | Ingestor name sent with every payload. Set a unique one per deployment, the default collides between installations |
| `REPEATER_NODE_ID` | no | empty | potato heartbeat: repeater node id (e.g. `!3c15e67e`) before the first `status`; learned automatically |
| `CHANNELS_RAW` | no | empty | Channel names to decrypt/forward, e.g. `test,news`. Empty = all public channels |
| `LOG_LEVEL` | no | `INFO` | Log level |

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
👤 ALEX KORO (b57eb3e8)
📡 #news
📶 Signal: SNR: -9.0dB / RSSI: -71dBm

💬 Text: Potato
```

Enable with `SINKS=telegram` (or `SINKS=telegram,potato` to also feed the map).

1. Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
2. Add the bot to your group/channel (as admin for channels).
3. Get the chat id (e.g. `-1001234567890`) and set `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID` in `.env` (both required).
4. Optionally limit channels with `TELEGRAM_CHANNELS_RAW=news,test`.

### Message template

`TELEGRAM_FORMAT` is a Telegram-HTML template (`<b>`, `<i>`, `<code>`, ...); write
`\n` for a line break. Placeholders (values are HTML-escaped automatically):

| Placeholder | Value |
|---|---|
| `{channel}` | channel label: `#news` for hashtag channels, `Public` for the Public channel |
| `{sender}` | sender display name |
| `{text}` | message body |
| `{repeater}` | name of the repeater that heard it (empty until its first `status`) |
| `{snr}` / `{rssi}` | signal quality, e.g. `4.0` / `-80` |
| `{freq}` / `{preset}` | LoRa frequency in MHz / preset, e.g. `869.618` / `SF8/BW62.5/CR8` |
| `{time}` | reception time, UTC `HH:MM:SS UTC` |
| `{from_id}` | sender node id without the `!`, e.g. `aabbccdd` |

A line whose placeholders are all empty is dropped, a separator left
dangling by an empty field is trimmed, and literal blank lines are kept. An unknown placeholder fails fast at
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
3. Enable it: `SINKS=telegram,<name>`.

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
