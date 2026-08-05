"""Состояние бота: подписчики, слепки афиши, ручные вердикты."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List

import config

_LOCK = threading.RLock()

_DEFAULT: Dict[str, Any] = {
    "subscribers": [],
    "events": {},        # site -> uuid -> snapshot
    "verdicts": {},      # "site:uuid" -> "yes" | "no"
    "muted": [],         # "site:uuid"
    "tickets_notified": [],  # про билеты на эти фильмы уже сообщали
    "seen_raw_hits": [], # хэши сырых совпадений со страницы
    "extra_keywords": [],
    "last_check": 0,
    "last_error": "",
    "stats": {"checks": 0, "alerts": 0, "errors": 0},
    "initialized": {},   # site -> bool
}


def _read() -> Dict[str, Any]:
    if not config.STATE_FILE.exists():
        return json.loads(json.dumps(_DEFAULT))
    try:
        data = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_DEFAULT))
    for key, value in _DEFAULT.items():
        data.setdefault(key, json.loads(json.dumps(value)))
    return data


def _write(state: Dict[str, Any]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, config.STATE_FILE)


class Storage:
    """Тонкая обёртка: всё состояние в одном JSON, доступ под общим локом."""

    def __init__(self) -> None:
        with _LOCK:
            self._state = _read()

    # --- общий доступ ---------------------------------------------------
    def save(self) -> None:
        with _LOCK:
            _write(self._state)

    @property
    def state(self) -> Dict[str, Any]:
        return self._state

    # --- подписчики -----------------------------------------------------
    @property
    def subscribers(self) -> List[int]:
        return list(self._state["subscribers"])

    def subscribe(self, chat_id: int) -> bool:
        with _LOCK:
            if chat_id in self._state["subscribers"]:
                return False
            self._state["subscribers"].append(chat_id)
            self.save()
            return True

    def unsubscribe(self, chat_id: int) -> bool:
        with _LOCK:
            if chat_id not in self._state["subscribers"]:
                return False
            self._state["subscribers"].remove(chat_id)
            self.save()
            return True

    # --- афиша ----------------------------------------------------------
    def site_events(self, site: str) -> Dict[str, Any]:
        return self._state["events"].setdefault(site, {})

    def is_initialized(self, site: str) -> bool:
        return bool(self._state["initialized"].get(site))

    def mark_initialized(self, site: str) -> None:
        self._state["initialized"][site] = True

    # --- вердикты и мьюты ------------------------------------------------
    def verdict(self, key: str) -> str:
        return self._state["verdicts"].get(key, "")

    def set_verdict(self, key: str, value: str) -> None:
        with _LOCK:
            self._state["verdicts"][key] = value
            self.save()

    def is_muted(self, key: str) -> bool:
        return key in self._state["muted"] or self.verdict(key) == "no"

    def mute(self, key: str) -> None:
        with _LOCK:
            if key not in self._state["muted"]:
                self._state["muted"].append(key)
                self.save()

    def unmute(self, key: str) -> None:
        with _LOCK:
            if key in self._state["muted"]:
                self._state["muted"].remove(key)
            self._state["verdicts"].pop(key, None)
            self.save()

    # --- ключевые слова --------------------------------------------------
    @property
    def keywords(self) -> List[str]:
        return list(self._state["extra_keywords"])

    def add_keyword(self, word: str) -> bool:
        word = word.strip().lower()
        with _LOCK:
            if not word or word in self._state["extra_keywords"]:
                return False
            self._state["extra_keywords"].append(word)
            self.save()
            return True

    def remove_keyword(self, word: str) -> bool:
        word = word.strip().lower()
        with _LOCK:
            if word not in self._state["extra_keywords"]:
                return False
            self._state["extra_keywords"].remove(word)
            self.save()
            return True

    # --- билеты ------------------------------------------------------------
    def tickets_notified(self, key: str) -> bool:
        return key in self._state["tickets_notified"]

    def mark_tickets(self, key: str) -> None:
        with _LOCK:
            if key not in self._state["tickets_notified"]:
                self._state["tickets_notified"].append(key)
                self.save()

    def clear_tickets(self, key: str) -> None:
        with _LOCK:
            if key in self._state["tickets_notified"]:
                self._state["tickets_notified"].remove(key)
                self.save()

    # --- сырые совпадения -------------------------------------------------
    def seen_raw_hit(self, digest: str) -> bool:
        return digest in self._state["seen_raw_hits"]

    def add_raw_hit(self, digest: str) -> None:
        with _LOCK:
            self._state["seen_raw_hits"].append(digest)
            self._state["seen_raw_hits"] = self._state["seen_raw_hits"][-200:]
            self.save()

    # --- статистика --------------------------------------------------------
    def bump(self, counter: str, amount: int = 1) -> None:
        with _LOCK:
            self._state["stats"][counter] = self._state["stats"].get(counter, 0) + amount
            self.save()
