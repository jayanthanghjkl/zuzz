"""Communication Hub - format messages per channel."""

from __future__ import annotations

from typing import Any, Dict, Optional


def format_telegram(text: str, buttons: Optional[list] = None) -> Dict[str, Any]:
    """Format a message for Telegram.
    
    Args:
        text: The message text.
        buttons: Optional list of button labels for inline keyboard.
    
    Returns:
        Formatted message dict.
    """
    msg: Dict[str, Any] = {"text": text, "parse_mode": "HTML"}
    if buttons:
        msg["reply_markup"] = {
            "inline_keyboard": [[{"text": b, "callback_data": b}] for b in buttons]
        }
    return msg


def format_email(subject: str, body: str, to: Optional[str] = None) -> Dict[str, Any]:
    """Format a message for email.
    
    Args:
        subject: Email subject.
        body: Email body.
        to: Optional recipient.
    
    Returns:
        Formatted email dict.
    """
    return {"subject": subject, "body": body, "to": to}


def format_push(title: str, message: str, priority: str = "normal") -> Dict[str, Any]:
    """Format a push notification.
    
    Args:
        title: Notification title.
        message: Notification message.
        priority: Priority level (low, normal, high, urgent).
    
    Returns:
        Formatted push notification dict.
    """
    return {"title": title, "message": message, "priority": priority}


def format_briefing(summary: str, sections: Optional[list] = None) -> str:
    """Format a daily briefing email body.
    
    Args:
        summary: Brief summary text.
        sections: Optional list of section titles.
    
    Returns:
        Formatted briefing text.
    """
    parts = [summary]
    if sections:
        parts.extend(f"### {s}" for s in sections)
    return "\n\n".join(parts)
