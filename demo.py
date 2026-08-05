#!/usr/bin/env python3
"""Присылает подписчикам образцы всех уведомлений на выдуманных данных.

Нужен, чтобы посмотреть оформление, не дожидаясь реальных событий.
Кнопки в образцах намеренно обезврежены: нажатие ничего не сохраняет.

Запуск:  python3 demo.py
"""

from __future__ import annotations

import copy
import sys
import time

import config
from src import checker, matcher, notifier
from src.bot import HELP
from src.parser import Film
from src.storage import Storage
from src.telegram_api import Telegram

PAUSE = 1.2  # чтобы не упереться в лимит Telegram

SHOWS = [
    {"id": "s1", "day": "2026/08/05", "time": "10:20", "sort": 1, "price": 450,
     "hall": "1", "formats": ["2D"], "vip": False, "sold_out": False},
    {"id": "s2", "day": "2026/08/05", "time": "13:40", "sort": 2, "price": 500,
     "hall": "1", "formats": ["2D"], "vip": False, "sold_out": True},
    {"id": "s3", "day": "2026/08/05", "time": "19:30", "sort": 3, "price": 550,
     "hall": "2", "formats": ["IMAX"], "vip": False, "sold_out": False},
    {"id": "s4", "day": "2026/08/06", "time": "11:00", "sort": 4, "price": 450,
     "hall": "2", "formats": ["IMAX"], "vip": False, "sold_out": False},
    {"id": "s5", "day": "2026/08/06", "time": "16:15", "sort": 5, "price": 500,
     "hall": "1", "formats": ["2D"], "vip": False, "sold_out": False},
]


def target(**overrides) -> Film:
    base = dict(
        site="vershina", site_name="Вершина", uuid="demo-uuid", href="chelovek-pauk-novyj-den-2026",
        url="https://kino-vershina.ru/events/chelovek-pauk-novyj-den-2026",
        name="Человек-паук: Новый день", status="rental", kind="FILM",
        show_count=len(SHOWS), show_count_today=3, show_count_days=2,
        on_screen="30.07.2026", date_start="15.07.2026", age_rest="12", duration=128,
        description=(
            "Питер Паркер начинает жизнь с чистого листа, но прошлое не отпускает: "
            "в Нью-Йорке появляется новая угроза, справиться с которой в одиночку не выйдет."
        ),
        genres=["фантастика", "боевик", "приключения"], countries=["США"],
        cast=["Том Холланд", "Сэди Синк", "Марк Руффало", "Джон Бернтал"],
        director=["Дестин Дэниел Креттон"], producer=["Кевин Файги", "Эми Паскаль"],
        poster="", dates=["2026/08/05", "2026/08/06"], shows=copy.deepcopy(SHOWS),
        formats=["2D", "IMAX"], min_price=450,
    )
    base.update(overrides)
    return Film(**base)


def safe_keyboard(film: Film):
    """Та же клавиатура, но callback-и обезврежены: ничего не сохранится."""
    keyboard = notifier.verdict_keyboard(film.key, film.url)
    for row in keyboard["inline_keyboard"]:
        for button in row:
            if "callback_data" in button:
                button["callback_data"] = "demo"
    return keyboard


def main() -> int:
    if not config.TELEGRAM_TOKEN:
        print("нет TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 1

    tg = Telegram(config.TELEGRAM_TOKEN)
    store = Storage()
    chats = store.subscribers
    if not chats:
        print("нет подписчиков: откройте бота и нажмите /start", file=sys.stderr)
        return 1

    full = target()
    verdict_high = matcher.match(full)
    verdict_medium = matcher.MatchResult("medium", 6, [
        "Слово «паук» в названии",
        "Знакомые актёры и продюсеры: Том Холланд",
    ])
    announced = target(show_count=0, shows=[], min_price=None, status="soon",
                       show_count_days=0, dates=[])

    # (подпись для вас, текст сообщения, клавиатура)
    samples = [
        ("1/12. Главное: билеты на цель поступили в продажу",
         notifier.format_alert("tickets", full, verdict_high,
                               "Появились сеансы, билеты можно брать."),
         safe_keyboard(full)),

        ("2/12. То же, но уверенность средняя",
         notifier.format_alert("tickets", full, verdict_medium,
                               "Появились сеансы, билеты можно брать."),
         safe_keyboard(full)),

        ("3/12. Фильм появился в афише, билетов ещё нет",
         notifier.format_alert("new", announced, verdict_high), safe_keyboard(announced)),

        ("4/12. К цели добавили новые сеансы",
         notifier.format_alert("more_shows", full, verdict_high,
                               "К уже известным сеансам добавились новые."),
         safe_keyboard(full)),

        ("5/12. Цель переименовали",
         notifier.format_alert("renamed", full, verdict_high,
                               "Было: «Новый день». Стало: «Человек-паук: Новый день»"),
         safe_keyboard(full)),

        ("6/12. Сменился статус проката",
         notifier.format_alert("status", full, verdict_high,
                               "Было: скоро. Стало: в прокате"),
         safe_keyboard(full)),

        ("7/12. Упоминание на сайте вне расписания",
         notifier.format_raw_hit(
             "Вершина", "https://kino-vershina.ru/",
             ["уже скоро в нашем кинотеатре человек паук новыи день не пропустите"]),
         None),

        ("8/12. Карточка из /candidates, с разбором алгоритма",
         notifier.format_card(full, verdict_high,
                              extra_block=notifier.format_reasons(verdict_high)),
         safe_keyboard(full)),

        ("9/12. Тихая сводка про посторонние новинки (приходит без звука)",
         "В афише «Вершина» появились новые фильмы (2), на цель не похожи: "
         "«Холоп 3», «Миньоны и монстры».", None),

        ("10/12. Цель пропала из афиши",
         "<b>Человек-паук: Новый день</b> пропал из афиши «Вершина».", None),

        ("11/12. Сообщение о проблеме с сайтом",
         "<b>Проблемы при проверке</b>\n• Другар: не удалось разобрать страницу. "
         "в пейлоаде не найдено ни одного фильма", None),

        ("12/12. Справка /help", HELP, notifier.reply_keyboard()),
    ]

    for chat_id in chats:
        tg.send_message(
            chat_id,
            "<b>Образцы уведомлений</b>\nНиже все виды сообщений на выдуманных данных. "
            "Кнопки в образцах не работают, ничего не сохранится.",
        )
        time.sleep(PAUSE)
        for caption, text, keyboard in samples:
            tg.send_message(chat_id, f"<b>{caption}</b>", disable_notification=True)
            time.sleep(0.4)
            tg.send_message(chat_id, text, keyboard=keyboard, disable_notification=True)
            time.sleep(PAUSE)

        # Живые ответы на команды, на настоящих данных сайтов.
        tg.send_message(chat_id, "<b>Дальше живые ответы на команды</b>", disable_notification=True)
        report = checker.run_check(store)
        time.sleep(PAUSE)
        tg.send_message(chat_id, "<b>Ответ на /check</b>", disable_notification=True)
        from src.bot import Bot  # импорт здесь: Bot создаёт своё подключение
        bot = Bot()
        bot.last_films = report.films_by_site
        tg.send_message(chat_id, bot.target_summary(report), disable_notification=True)
        time.sleep(PAUSE)
        tg.send_message(chat_id, "<b>Ответ на /list</b>", disable_notification=True)
        tg.send_message(chat_id, notifier.format_list(report.films_by_site),
                        disable_notification=True)

    print("отправлено получателям:", chats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
