"""Конфигурация бота. Секреты берутся из .env / переменных окружения."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "bot.log"


def _load_dotenv(path: Path) -> None:
    """Минималистичный .env-лоадер, чтобы не тянуть python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# Интервал проверки сайтов, секунд (по умолчанию 10 минут).
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "600"))

# Интервал, когда цель уже найдена в афише: билеты разбирают быстро.
CHECK_INTERVAL_HOT = int(os.environ.get("CHECK_INTERVAL_HOT", "120"))

# Присылать ли тихую строчку про новые посторонние фильмы (1/0).
# Нужна как страховка: если название замаскировано так, что алгоритм его
# не узнал, вы всё равно увидите, что в афише появилось что-то новое.
NEW_FILM_DIGEST = os.environ.get("NEW_FILM_DIGEST", "1") not in ("0", "false", "no")

# Таймаут HTTP-запросов к сайтам кинотеатров.
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))

# Кому слать технические ошибки (0 значит всем подписчикам).
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0") or 0)

# Отслеживаемые площадки.
SITES = {
    "vershina": {
        "name": "Вершина",
        "url": "https://kino-vershina.ru/",
        "event_url": "https://kino-vershina.ru/events/{href}",
    },
    "drugar": {
        "name": "Другар",
        "url": "https://drugar.ru/",
        "event_url": "https://drugar.ru/events/{href}",
    },
}
