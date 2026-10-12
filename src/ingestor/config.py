from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_TELEGRAM_FORMAT = (
    "<b>#{channel}</b> · {sender}\n"
    "{text}\n"
    "<i>{repeater} · SNR {snr} · RSSI {rssi}</i>"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # MQTT
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str
    mqtt_password: str
    mqtt_topic_prefix: str = "meshcore"
    # Shared subscription group — all replicas share load, each message processed once
    mqtt_consumer_group: str = "ingestor-group"

    # Comma-separated sinks that receive events, e.g. "potato" or "potato,telegram"
    sinks: str = "potato"

    # potato-mesh sink (required when "potato" is in SINKS)
    potato_host: str = ""
    potato_api_token: str = ""
    # Name shown in the potato-mesh ingestors list
    potato_ingestor_name: str = "mqtt-ingestor"

    # Telegram sink (required when "telegram" is in SINKS)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Comma-separated channel names to forward to Telegram; empty = all decrypted channels
    telegram_channels_raw: str = ""
    # Message template (Telegram HTML). Placeholders: {channel} {sender} {text} {repeater}
    # {snr} {rssi} {freq} {preset} {time} {from_id}. Use \\n for a line break.
    telegram_format: str = DEFAULT_TELEGRAM_FORMAT

    # Comma-separated public channel names (without #), e.g. "test,news,general".
    # Keys are derived automatically from names.
    # Empty = forward all public channels (decrypt where possible).
    channels_raw: str = ""

    # Optional: pre-configure the repeater's canonical node_id (e.g. !3c15e67e)
    # Used for heartbeat before the first status message arrives.
    # Learned automatically from status messages at runtime.
    repeater_node_id: str = ""

    log_level: str = "INFO"

    @property
    def channels(self) -> list[str]:
        raw = self.channels_raw.strip()
        return [n.strip() for n in raw.split(",") if n.strip()] if raw else []

    @property
    def telegram_channels(self) -> list[str]:
        raw = self.telegram_channels_raw.strip()
        return [n.strip() for n in raw.split(",") if n.strip()] if raw else []

    @property
    def enabled_sinks(self) -> list[str]:
        return [n.strip().lower() for n in self.sinks.split(",") if n.strip()]

    @property
    def mqtt_shared_topic(self) -> str:
        return f"$share/{self.mqtt_consumer_group}/{self.mqtt_topic_prefix}/#"


settings = Settings()
