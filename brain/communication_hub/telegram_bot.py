"""Communication Hub - Telegram bot integration."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from brain.communication_hub.exceptions import TelegramError
from brain.communication_hub.response_tracker import ResponseTracker

logger = logging.getLogger(__name__)


class TelegramBot:
    """Wrapper around python-telegram-bot for ZUZZ."""

    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            logger.warning("TELEGRAM_BOT_TOKEN not set")
        self._token = token
        self._application: Optional[Application] = None
        self._response_tracker = ResponseTracker()
        self._pending_approvals: Dict[str, asyncio.Future] = {}
        self._listening = False

    def is_configured(self) -> bool:
        """Return True when a bot token is available."""
        return bool(self._token)

    async def start(self) -> None:
        """Start the bot if configured."""
        if not self.is_configured():
            return
        if self._application is not None:
            return
        self._application = Application.builder().token(self._token).build()
        self._application.add_handler(CommandHandler("start", self._cmd_start))
        self._application.add_handler(CommandHandler("help", self._cmd_help))
        self._application.add_handler(CallbackQueryHandler(self._on_callback))
        await self._application.initialize()
        await self._application.start()
        self._listening = True
        logger.info("telegram bot started")

    async def stop(self) -> None:
        """Stop the bot."""
        if self._application is None:
            return
        self._listening = False
        await self._application.stop()
        await self._application.shutdown()
        self._application = None
        logger.info("telegram bot stopped")

    async def send_message(self, chat_id: str, text: str, buttons: Optional[list] = None) -> Dict[str, Any]:
        """Send a Telegram message.
        
        Args:
            chat_id: Target chat ID.
            text: Message text.
            buttons: Optional button labels.
        
        Returns:
            Result dict with message_id.
        
        Raises:
            TelegramError: On send failure.
        """
        if not self.is_configured():
            raise TelegramError("telegram not configured")
        try:
            from telegram import InlineKeyboardMarkup
            markup = None
            if buttons:
                markup = InlineKeyboardMarkup(
                    [[{"text": b, "callback_data": b}] for b in buttons]
                )
            msg = await self._application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return {"message_id": msg.message_id, "chat_id": chat_id}
        except Exception as exc:
            raise TelegramError(f"failed to send telegram message: {exc}") from exc

    async def send_file(self, chat_id: str, file_path: str, caption: str = "") -> Dict[str, Any]:
        """Send a file via Telegram.
        
        Args:
            chat_id: Target chat ID.
            file_path: Path to file.
            caption: Optional caption.
        
        Returns:
            Result dict with message_id.
        """
        if not self.is_configured():
            raise TelegramError("telegram not configured")
        try:
            with open(file_path, "rb") as fh:
                msg = await self._application.bot.send_document(
                    chat_id=chat_id, document=fh, caption=caption
                )
            return {"message_id": msg.message_id, "chat_id": chat_id}
        except Exception as exc:
            raise TelegramError(f"failed to send telegram file: {exc}") from exc

    async def request_approval(self, chat_id: str, action: str, timeout_seconds: int = 300) -> Optional[bool]:
        """Send an approval request and wait for response.
        
        Args:
            chat_id: Target chat ID.
            action: Description of the action.
            timeout_seconds: How long to wait.
        
        Returns:
            True if approved, False if denied, None if timeout.
        """
        request_id = f"approve_{action}_{int(time.time())}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_approvals[request_id] = future
        self._response_tracker.register(request_id, timeout_seconds)
        try:
            await self.send_message(
                chat_id=chat_id,
                text=f"Approval needed: {action}\nReply Yes or No",
                buttons=["Yes", "No"],
            )
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_approvals.pop(request_id, None)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        await update.message.reply_text("ZUZZ is online. I will send you updates here.")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        await update.message.reply_text("ZUZZ commands: /start, /help")

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle button callbacks."""
        query = update.callback_query
        if query is None:
            return
        data = query.data
        chat_id = str(query.message.chat_id) if query.message else ""
        approved = data.lower() == "yes"
        for rid, future in list(self._pending_approvals.items()):
            if not future.done():
                future.set_result(approved)
                break
        await query.answer_text(f"Response received: {data}")
