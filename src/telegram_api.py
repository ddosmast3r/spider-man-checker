"""Минимальный клиент Telegram Bot API на requests (long polling)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("telegram")


class TelegramError(RuntimeError):
    pass


def escape(text: str) -> str:
    """Экранирование для parse_mode=HTML."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class Telegram:
    def __init__(self, token: str) -> None:
        if not token:
            raise TelegramError("не задан TELEGRAM_BOT_TOKEN")
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()

    def call(self, method: str, _http_timeout: int = 30, **params: Any) -> Any:
        """Вызов метода API. `_http_timeout` — таймаут сокета, остальное уходит в тело запроса."""
        url = f"{self.base}/{method}"
        for attempt in range(3):
            try:
                response = self.session.post(url, json=params, timeout=_http_timeout)
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("%s: сеть — %s (попытка %s)", method, exc, attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue
            if data.get("ok"):
                return data.get("result")
            description = data.get("description", "")
            if response.status_code == 429:
                retry_after = int((data.get("parameters") or {}).get("retry_after", 3))
                log.warning("%s: лимит, ждём %ss", method, retry_after)
                time.sleep(retry_after + 1)
                continue
            # 403 — пользователь заблокировал бота; это не повод падать.
            if response.status_code in (400, 403):
                raise TelegramError(description)
            log.warning("%s: %s", method, description)
            time.sleep(2)
        raise TelegramError(f"{method}: не удалось выполнить запрос")

    # --- методы, которые реально нужны боту ---------------------------------
    def get_updates(self, offset: int, timeout: int = 30) -> List[Dict[str, Any]]:
        return self.call(
            "getUpdates",
            _http_timeout=timeout + 15,
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query"],
        ) or []

    def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: Optional[Dict[str, Any]] = None,
        disable_notification: bool = False,
        preview: bool = False,
    ) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
            "disable_notification": disable_notification,
        }
        if keyboard:
            params["reply_markup"] = keyboard
        try:
            return self.call("sendMessage", **params)
        except TelegramError as exc:
            log.warning("не отправлено в %s: %s", chat_id, exc)
            return None

    def send_photo(
        self,
        chat_id: int,
        photo: str,
        caption: str,
        keyboard: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption[:1024],
            "parse_mode": "HTML",
        }
        if keyboard:
            params["reply_markup"] = keyboard
        try:
            return self.call("sendPhoto", **params)
        except TelegramError as exc:
            log.warning("фото не отправлено в %s: %s", chat_id, exc)
            return None

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200], show_alert=alert)
        except TelegramError:
            pass

    def edit_markup(self, chat_id: int, message_id: int, keyboard: Optional[Dict[str, Any]]) -> None:
        try:
            self.call(
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard or {"inline_keyboard": []},
            )
        except TelegramError:
            pass

    def set_commands(self, commands: List[Dict[str, str]]) -> None:
        try:
            self.call("setMyCommands", commands=commands)
        except TelegramError:
            pass

    def get_me(self) -> Dict[str, Any]:
        return self.call("getMe")
