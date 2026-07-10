"""Отправка пушей в групповой чат Telegram (§6 ТЗ)."""
from __future__ import annotations

import html
import logging

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 30


def format_job_message(fields: dict) -> str:
    """HTML-сообщение по шаблону §6 ТЗ. Все значения экранируются."""

    def esc(key: str, default: str = "—") -> str:
        value = fields.get(key)
        return html.escape(str(value)) if value not in (None, "") else default

    return (
        f"🟢 <b>{esc('Category')}</b>\n"
        f"<b>{esc('Title')}</b>\n"
        f"💰 {esc('Budget')} · 🌍 {esc('Country')}\n"
        f"🧠 {esc('Summary RU')}\n"
        f"\n"
        f"🔗 {esc('URL')}"
    )


def send_job_notification(bot_token: str, chat_id: str, fields: dict) -> bool:
    """Один пуш на запись. False при ошибке — Notified не проставляется,
    пуш будет повторён на следующем прогоне."""
    try:
        resp = requests.post(
            API_URL.format(token=bot_token),
            json={
                "chat_id": chat_id,
                "text": format_job_message(fields),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        if not resp.json().get("ok"):
            log.error("Telegram вернул ошибку: %s", resp.text)
            return False
        return True
    except Exception:
        log.error("Не удалось отправить пуш для %r", fields.get("Title"), exc_info=True)
        return False
