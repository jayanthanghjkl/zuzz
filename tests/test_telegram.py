"""Tests for the Telegram bot integration."""

from __future__ import annotations

import pytest

from brain.communication_hub.exceptions import TelegramError
from brain.communication_hub.telegram_bot import TelegramBot


class DummyUpdate:
    class Message:
        def reply_text(self, text):
            return self

    message = Message()


class DummyContext:
    pass


@pytest.fixture
def telegram_bot():
    bot = TelegramBot()
    return bot


def test_telegram_bot_not_configured_without_token(monkeypatch):
    """Without a token the bot reports not configured."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bot = TelegramBot()
    assert not bot.is_configured()


def test_telegram_bot_configured_with_token(monkeypatch):
    """With a token the bot reports configured."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    bot = TelegramBot()
    assert bot.is_configured()


@pytest.mark.asyncio
async def test_telegram_bot_sends_message(monkeypatch):
    """When configured the bot can send a message."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    bot = TelegramBot()

    class FakeApp:
        class Bot:
            async def send_message(self, chat_id, text, parse_mode, reply_markup=None):
                return type("Msg", (), {"message_id": 1})()

        bot = Bot()

    bot._application = FakeApp()
    result = await bot.send_message("123", "hello", buttons=["Yes"])
    assert result["message_id"] == 1
    assert result["chat_id"] == "123"


def test_telegram_bot_send_raises_without_config():
    """Sending without config raises TelegramError."""
    bot = TelegramBot()
    with pytest.raises(TelegramError):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.send_message("123", "hello"))
