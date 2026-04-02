from __future__ import annotations

import pytest
from pydantic import ValidationError

from arcane_mage.models import DiscordNotification, Notifications, TelegramNotification


class TestDiscordNotification:
    def test_valid_webhook(self):
        notif = DiscordNotification(
            webhook_url="https://discord.com/api/webhooks/123456789/abcdefg",
            user_id="321487457843565639",
        )
        assert notif.webhook_url is not None

    def test_invalid_webhook_host(self):
        with pytest.raises(ValidationError):
            DiscordNotification(webhook_url="https://example.com/api/webhooks/123/abc")

    def test_invalid_webhook_path(self):
        with pytest.raises(ValidationError):
            DiscordNotification(webhook_url="https://discord.com/other/path")

    def test_invalid_webhook_scheme(self):
        with pytest.raises(ValidationError):
            DiscordNotification(webhook_url="http://discord.com/api/webhooks/123/abc")

    def test_user_id_too_short(self):
        with pytest.raises(ValidationError):
            DiscordNotification(user_id="1234")

    def test_user_id_too_long(self):
        with pytest.raises(ValidationError):
            DiscordNotification(user_id="1" * 20)

    def test_none_values_default(self):
        notif = DiscordNotification()
        assert notif.webhook_url is None
        assert notif.user_id is None

    def test_to_dict_excludes_none(self):
        notif = DiscordNotification()
        assert notif.to_dict() == {}


class TestTelegramNotification:
    def test_valid(self):
        notif = TelegramNotification(
            bot_token="1234567890:ABCDefghijklmnopqrstuvwxyz1234567-_",
            chat_id="123456789",
        )
        assert notif.telegram_alert == "1"

    def test_invalid_bot_token_pattern(self):
        with pytest.raises(ValidationError):
            TelegramNotification(bot_token="invalid-token")

    def test_telegram_alert_disabled(self):
        notif = TelegramNotification()
        assert notif.telegram_alert == "0"


class TestNotifications:
    def test_from_dict_empty(self):
        notif = Notifications.from_dict({})

        assert notif.discord is not None
        assert notif.telegram is not None
        assert notif.email is None

    def test_from_dict_with_discord(self):
        data = {
            "discord": {
                "webhook_url": "https://discord.com/api/webhooks/123456789/abcdefg",
                "user_id": "321487457843565639",
            }
        }
        notif = Notifications.from_dict(data)

        assert notif.discord.webhook_url is not None

    def test_webhook_validation(self):
        with pytest.raises(ValidationError):
            Notifications(webhook="not-a-url")

    def test_to_dict_excludes_empty(self):
        notif = Notifications()
        result = notif.to_dict()

        # Empty discord/telegram should not appear
        assert "discord" not in result
        assert "telegram" not in result
