#!/usr/bin/env python3
"""Точка входа: запуск телеграм-бота «Спайдер-чекер»."""

from __future__ import annotations

import logging
import sys

import config


def setup_logging() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
        datefmt="%d.%m %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        ],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    setup_logging()
    if not config.TELEGRAM_TOKEN:
        print(
            "Не задан TELEGRAM_BOT_TOKEN.\n"
            "Создайте файл .env рядом с run.py:\n"
            "  TELEGRAM_BOT_TOKEN=123456:AA...\n",
            file=sys.stderr,
        )
        return 1

    from src.bot import Bot

    bot = Bot()
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop_event.set()
        logging.getLogger("bot").info("остановлен пользователем")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
