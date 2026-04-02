from __future__ import annotations

from dataclasses import fields
from typing import Annotated

from pydantic import EmailStr, TypeAdapter, field_validator
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass
from pydantic.networks import HttpUrl
from pydantic.types import StringConstraints


@py_dataclass
class DiscordNotification:
    """Discord webhook notification configuration for fluxnode alerts."""

    webhook_url: str | None = None
    user_id: str | None = None

    @field_validator("webhook_url", mode="after")
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        if not value:
            return value

        # this will raise Validation error (and be caught)
        url = HttpUrl(value)
        # discordapp.com is the deprecated endpoint
        valid_hosts = ["discordapp.com", "discord.com"]

        if url.host not in valid_hosts:
            raise ValueError("Discord webhook url must have discord as the host")

        if not url.scheme == "https":
            raise ValueError("discord webhook url scheme must be https")

        if not url.path or not url.path.startswith("/api/webhooks"):
            raise ValueError("discord webhook path must start with /api/webhooks")

        return value

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: str | int) -> str:
        if (not value and isinstance(value, str)) or value is None:
            return value

        as_str = str(value)

        len_user_id = len(as_str)

        if len_user_id < 17 or len_user_id > 19:
            raise ValueError("Discord user id must be between 17 and 19 characters")

        return as_str

    @classmethod
    def from_dict(cls, data: dict) -> DiscordNotification:
        items = []

        for _field in fields(cls):
            name = _field.name
            prop = data.get(name)

            items.append(prop)

        return cls(*items)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)


@py_dataclass
class TelegramNotification:
    """Telegram bot notification configuration for fluxnode alerts."""

    bot_token: str | None = Field(None, pattern=r"^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$")
    chat_id: str | None = Field(None, min_length=6, max_length=1000)

    @classmethod
    def from_dict(cls, data: dict) -> TelegramNotification:
        items = []

        for _field in fields(cls):
            name = _field.name
            prop = data.get(name)

            items.append(prop)

        return cls(*items)

    @property
    def telegram_alert(self) -> str:
        return "1" if self.bot_token and self.chat_id else "0"

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)


@py_dataclass
class Notifications:
    """Aggregated notification settings supporting Discord, Telegram, email, and webhooks."""

    discord: DiscordNotification = Field(default_factory=DiscordNotification)
    telegram: TelegramNotification = Field(default_factory=TelegramNotification)
    email: (
        Annotated[
            EmailStr,
            StringConstraints(strip_whitespace=True, to_lower=True),
        ]
        | None
    ) = None
    webhook: str | None = None
    node_name: str | None = None

    @field_validator("webhook", mode="after")
    @classmethod
    def validate_webhook(cls, value: str | None) -> str | None:
        if not value:
            return value

        # this will raise ValidationError for us
        HttpUrl(value)

        return value

    @classmethod
    def from_dict(cls, data: dict) -> Notifications:
        discord_raw = data.get("discord")
        telegram_raw = data.get("telegram")

        discord = DiscordNotification.from_dict(discord_raw) if discord_raw else DiscordNotification()

        telegram = TelegramNotification.from_dict(telegram_raw) if telegram_raw else TelegramNotification()

        other_items = {k: data.get(k) for k in ("email", "webhook", "node_name")}

        return cls(discord=discord, telegram=telegram, **other_items)

    def to_dict(self) -> dict:
        raw = TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)
        # Remove empty nested dicts (e.g. discord/telegram with no configured values)
        return {k: v for k, v in raw.items() if v}

