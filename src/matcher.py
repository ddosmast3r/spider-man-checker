"""Распознавание «Человек-Паук: Новый день» в карточке фильма.

Русское прокатное название заранее неизвестно и может отличаться от
оригинального (а у неофициального проката оно бывает вообще произвольным),
поэтому решение принимается не по одной строке, а по сумме признаков:
название, описание, актёры, режиссёр, страна/жанр/год.

Итог — уровень уверенности:
    high   — почти наверняка он, шлём громкий алерт;
    medium — похоже, нужна ручная проверка кнопками;
    low    — слабый сигнал, попадает в /candidates, но не будит ночью;
    none   — не он.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

TARGET = "Человек-Паук: Новый день"

# --- Признаки -----------------------------------------------------------

SPIDER_WORDS = (
    "паук", "паучок", "пауки", "паутин",
    "spider", "spiderman", "spider man", "спаидер", "спаидермен",
)
NEWDAY_WORDS = ("новыи день", "новыи денъ", "brand new day", "new day", "novyi den", "novyj den")

# Каст и создатели фильма — самый устойчивый признак: даже если прокатчик
# придумает своё название, состав в карточке обычно остаётся настоящим.
STRONG_PEOPLE = (
    "том холланд", "tom holland",
    "дестин дэниел креттон", "дестин дэниэл креттон", "дестин креттон",
    "destin daniel cretton", "destin cretton",
)
SUPPORT_PEOPLE = (
    "зендея", "zendaya",
    "джейкоб баталон", "jacob batalon",
    "сэди синк", "сади синк", "sadie sink",
    "марк руффало", "mark ruffalo",
    "джон бернтал", "jon bernthal",
    "майкл мандо", "michael mando",
    "кевин файги", "kevin feige", "эми паскаль", "amy pascal",
)
LORE_WORDS = (
    # Только однозначные маркеры. Голого «Паркера» здесь нет намеренно —
    # это распространённая фамилия и она даёт ложные срабатывания.
    "питер паркер", "peter parker",
    "мстител", "marvel", "марвел", "sony pictures",
)

# Признаки, специфичные ИМЕННО для франшизы. Это жёсткий фильтр: если в карточке
# нет ни одного из них, фильм не кандидат вообще — сколько бы знакомых актёров
# в нём ни снималось. Иначе в кандидаты лезет любой фильм с Томом Холландом
# (например, «Одиссея» Нолана — там же Холланд и Зендея).
FRANCHISE_MARKERS = SPIDER_WORDS + NEWDAY_WORDS + LORE_WORDS + (
    "дестин дэниел креттон", "дестин дэниэл креттон", "дестин креттон",
    "destin daniel cretton", "destin cretton",
)

# Уже вышедшие части франшизы — их регулярно ставят в повторный прокат.
# Совпадение с ними понижаем, чтобы не будить тревогой из-за ретро-показа.
KNOWN_OLD_TITLES = (
    "нет пути домои", "no way home",
    "вдали от дома", "far from home",
    "возвращение домои", "homecoming",
    "через вселенные", "into the spider verse",
    "паутина вселенных", "across the spider verse",
    "новыи человек паук", "amazing spider man",
    "высокое напряжение",
    "враг в отражении",
    "человек паук 2", "человек паук 3",
)

_LATIN_TO_CYR_HINTS = {
    "spider-man": "человек паук",
    "spiderman": "человек паук",
}


def normalize(text: str) -> str:
    """Приводит текст к виду, устойчивому к регистру, ё/е, дефисам и пунктуации."""
    text = (text or "").lower().replace("ё", "е").replace("й", "и")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _hit(haystack: str, needles: Iterable[str]) -> List[str]:
    return [n for n in needles if normalize(n) and normalize(n) in haystack]


def _names(found: Iterable[str], limit: int) -> str:
    """Имена для показа пользователю: с заглавной буквы, без повторов."""
    pretty = sorted({" ".join(w.capitalize() for w in n.split()) for n in found})
    return ", ".join(pretty[:limit])


@dataclass
class MatchResult:
    level: str  # high | medium | low | none
    score: int
    reasons: List[str]

    @property
    def is_candidate(self) -> bool:
        return self.level in ("high", "medium", "low")

    @property
    def label(self) -> str:
        return {
            "high": "почти наверняка он",
            "medium": "похоже на него, нужна проверка",
            "low": "слабое совпадение",
            "none": "не он",
        }[self.level]


def match_film(
    name: str,
    text: str,
    cast: Sequence[str] = (),
    director: Sequence[str] = (),
    year_hint: str = "",
    extra_keywords: Sequence[str] = (),
) -> MatchResult:
    """Считает уверенность в том, что перед нами «Человек-Паук: Новый день»."""
    norm_name = normalize(name)
    full = normalize(" ".join([name, text, " ".join(cast), " ".join(director), year_hint]))
    for latin, cyr in _LATIN_TO_CYR_HINTS.items():
        if normalize(latin) in full:
            full += " " + cyr

    score = 0
    reasons: List[str] = []

    spider_in_name = _hit(norm_name, SPIDER_WORDS)
    spider_anywhere = _hit(full, SPIDER_WORDS)
    newday_in_name = _hit(norm_name, NEWDAY_WORDS)
    newday_anywhere = _hit(full, NEWDAY_WORDS)

    if spider_in_name:
        score += 4
        reasons.append("Слово «паук» в названии")
    elif spider_anywhere:
        score += 2
        reasons.append("Слово «паук» в описании")

    if newday_in_name:
        score += 4
        reasons.append("«Новый день» в названии")
    elif newday_anywhere:
        score += 2
        reasons.append("«Новый день» в описании")

    strong = _hit(full, STRONG_PEOPLE)
    if strong:
        score += 5
        reasons.append("Ключевые люди фильма: " + _names(strong, 3))

    support = _hit(full, SUPPORT_PEOPLE)
    if support:
        score += min(4, 2 * len(set(support)))
        reasons.append("Знакомые актёры и продюсеры: " + _names(support, 4))

    lore = _hit(full, LORE_WORDS)
    if lore:
        score += min(3, len(set(lore)))
        reasons.append("Приметы вселенной: " + _names(lore, 3))

    custom = _hit(full, [k for k in extra_keywords if k.strip()])
    if custom:
        score += 4
        reasons.append("Ваши ключевые слова: " + ", ".join(custom))

    # Комбинация «паук + новый день» в любом виде — это уже практически прямое попадание.
    if spider_anywhere and newday_anywhere:
        score += 4
        reasons.append("Совпали оба ключевых элемента названия")

    if score >= 8:
        level = "high"
    elif score >= 5:
        level = "medium"
    elif score >= 2:
        level = "low"
    else:
        level = "none"

    # Жёсткий фильтр: нет ни одной приметы франшизы — не наш фильм, точка.
    # Один только знакомый состав ничего не значит: Том Холланд и Зендея
    # снимаются и в «Одиссее» Нолана, и в десятке других картин.
    if not _hit(full, FRANCHISE_MARKERS):
        return MatchResult("none", 0, ["Нет ни одной приметы франшизы, совпал только состав"])

    # Ретро-показ уже вышедшей части — не наша цель, если нет «Нового дня».
    old = _hit(norm_name, KNOWN_OLD_TITLES)
    if old and not newday_anywhere:
        level = "low"
        reasons.append(f"Это уже вышедшая часть франшизы: {old[0]}")

    return MatchResult(level=level, score=score, reasons=reasons)


def match(film, extra_keywords: Sequence[str] = ()) -> MatchResult:
    """Обёртка над match_film для объекта Film из parser.py."""
    return match_film(
        name=film.name,
        text=film.searchable_text(),
        cast=film.cast,
        director=film.director,
        year_hint=" ".join([film.on_screen, film.date_start]),
        extra_keywords=extra_keywords,
    )


def scan_raw_html(html: str) -> List[str]:
    """Запасной скан сырой страницы.

    Нужен на случай, если фильм анонсирован баннером/новостью и ещё не попал
    в структурированную афишу. Чтобы не ловить архивные ссылки на старые части,
    требуем оба элемента названия рядом друг с другом.
    """
    text = normalize(re.sub(r"<[^>]+>", " ", html))
    hits: List[str] = []
    for spider in SPIDER_WORDS:
        s_norm = normalize(spider)
        for m in re.finditer(re.escape(s_norm), text):
            window = text[max(0, m.start() - 120) : m.end() + 120]
            if any(normalize(nd) in window for nd in NEWDAY_WORDS):
                hits.append(window.strip())
    # схлопываем пересекающиеся окна
    unique: List[str] = []
    for h in hits:
        if not any(h in u or u in h for u in unique):
            unique.append(h)
    return unique[:5]
