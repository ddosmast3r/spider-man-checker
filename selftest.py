#!/usr/bin/env python3
"""Самопроверка без Telegram: парсинг сайтов + проверка алгоритма распознавания.

Запуск:  python3 selftest.py
"""

from __future__ import annotations

import sys

import config
from src import matcher
from src.parser import fetch_site

# Карточки-пустышки для проверки распознавания: как фильм может выглядеть
# в афише при разных вариантах локализации названия.
FAKES = [
    {
        "name": "Человек-паук: Новый день",
        "text": "Питер Паркер начинает жизнь заново. Marvel Studios и Sony Pictures.",
        "cast": ["Том Холланд", "Сэди Синк", "Марк Руффало"],
        "director": ["Дестин Дэниел Креттон"],
        "expect": "high",
    },
    {
        "name": "Spider-Man: Brand New Day",
        "text": "Peter Parker, Marvel Studios",
        "cast": ["Tom Holland"],
        "director": ["Destin Daniel Cretton"],
        "expect": "high",
    },
    {
        "name": "Новый день",  # прокатчик убрал «Человека-паука» из названия
        "text": "Приключенческий боевик о супергерое из Нью-Йорка.",
        "cast": ["Том Холланд", "Зендея"],
        "director": ["Дестин Креттон"],
        "expect": "high",
    },
    {
        "name": "Паутина судьбы",  # название замаскировано: ловим по «паутине» + касту
        "text": "Фантастический боевик, США, 2026.",
        "cast": ["Том Холланд", "Джейкоб Баталон"],
        "director": [],
        "expect": "high",  # «паутина» это примета франшизы, плюс двое из каста
    },
    {
        "name": "Одиссея",  # ловушка: у Нолана снимаются те же Холланд и Зендея
        "text": "Эпос по поэме Гомера.",
        "cast": ["Том Холланд", "Зендея", "Мэтт Дэймон"],
        "director": ["Кристофер Нолан"],
        "expect": "none",  # знакомый состав без примет франшизы, не наш фильм
    },
    {
        "name": "Хороший день",  # «день» есть, но никаких примет франшизы
        "text": "Драма о жизни в маленьком городе.",
        "cast": ["Том Холланд"],
        "director": [],
        "expect": "none",
    },
    {
        "name": "Новый день",  # то же название, но с Креттоном, это уже он
        "text": "Приключенческий боевик о супергерое из Нью-Йорка.",
        "cast": ["Том Холланд", "Зендея"],
        "director": ["Дестин Креттон"],
        "expect": "high",
    },
    {
        "name": "Человек-паук: Через вселенные",  # старый мультфильм, не цель
        "text": "Майлз Моралес и мультивселенная.",
        "cast": [],
        "director": [],
        "expect": "low",
    },
    {
        "name": "Человек-паук: Нет пути домой",  # ретро-показ прошлой части
        "text": "Питер Паркер, Marvel, мультивселенная.",
        "cast": ["Том Холланд", "Зендея"],
        "director": ["Джон Уоттс"],
        "expect": "low",
    },
    {
        "name": "Холоп 3",
        "text": "Комедия про перевоспитание мажора.",
        "cast": ["Милош Бикович"],
        "director": ["Клим Шипенко"],
        "expect": "none",
    },
    {
        "name": "Миньоны и монстры",
        "text": "1920-е годы. Миньоны снимаются в кино и покоряют Голливуд.",
        "cast": ["Пьер Коффан"],
        "director": ["Пьер Коффан"],
        "expect": "none",
    },
]


def test_matcher() -> int:
    print("=== Проверка алгоритма распознавания ===")
    failures = 0
    for fake in FAKES:
        result = matcher.match_film(
            name=fake["name"],
            text=fake["text"],
            cast=fake["cast"],
            director=fake["director"],
        )
        ok = result.level == fake["expect"]
        failures += 0 if ok else 1
        mark = "✓" if ok else "✗"
        print(
            f"{mark} {fake['name']:<38} → {result.level:<7} ({result.score:>2} б.) "
            f"ожидалось: {fake['expect']}"
        )
        if not ok:
            print(f"    признаки: {'; '.join(result.reasons) or 'нет'}")
    return failures


def test_sites() -> int:
    print("\n=== Проверка парсинга сайтов ===")
    failures = 0
    for site_key, site in config.SITES.items():
        try:
            snapshot = fetch_site(site_key)
        except Exception as exc:
            print(f"✗ {site['name']}: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        print(f"✓ {site['name']}: {len(snapshot.films)} фильмов")
        for film in snapshot.films[:5]:
            price = f", от {film.min_price} ₽" if film.min_price else ""
            verdict = matcher.match(film)
            flag = f"  {verdict.emoji} {verdict.level}" if verdict.is_candidate else ""
            print(f"    • {film.name}, {film.show_count} сеанс.{price}{flag}")
        if len(snapshot.films) > 5:
            print(f"    … ещё {len(snapshot.films) - 5}")

        candidates = [f for f in snapshot.films if matcher.match(f).is_candidate]
        print(f"    Паучьих совпадений в афише: {len(candidates)}")
        raw = matcher.scan_raw_html(snapshot.html)
        print(f"    Совпадений «паук + новый день» в сырой странице: {len(raw)}")
    return failures


def main() -> int:
    failures = test_matcher() + test_sites()
    print()
    print("✅ Все проверки пройдены" if not failures else f"❌ Провалено проверок: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
