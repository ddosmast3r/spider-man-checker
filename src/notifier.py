"""Формирование текстов уведомлений и клавиатур.

Правила оформления, принятые для этого бота:
  * эмодзи как маркер строки, дальше жирная подпись и значение;
  * без длинных тире: разделяем запятой, двоеточием или скобками;
  * разметка Telegram (parse_mode=HTML): <b> подписи, <code> даты,
    <blockquote> описание, <a href> ссылки вместо голых URL.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.matcher import MatchResult
from src.parser import Film
from src.telegram_api import escape

CHANGE_TITLES = {
    "new": "🆕 Новый фильм в афише",
    "tickets": "🎟 БИЛЕТЫ В ПРОДАЖЕ",
    "on_sale": "🎟 Билеты поступили в продажу",
    "more_shows": "➕ Добавили сеансы",
    "renamed": "✏️ Фильм переименован",
    "status": "🔄 Изменился статус проката",
    "new_dates": "📆 Добавлены новые даты",
    "gone": "🗑 Фильм пропал из афиши",
}

# Изменения, для которых в карточку подставляется расписание.
SHOW_CHANGES = {"tickets", "on_sale", "more_shows", "new", "new_dates"}

CONFIDENCE_ICONS = {"high": "🕷", "medium": "🕷", "low": "❓"}

CONFIDENCE_LABELS = {
    "high": "высокая",
    "medium": "средняя, стоит проверить",
    "low": "низкая",
}


def plural(number: int, one: str, few: str, many: str) -> str:
    """Согласует существительное с числом: 1 фильм, 2 фильма, 5 фильмов."""
    n = abs(number) % 100
    if 11 <= n <= 14:
        word = many
    elif n % 10 == 1:
        word = one
    elif 2 <= n % 10 <= 4:
        word = few
    else:
        word = many
    return f"{number} {word}"


def films_count(number: int) -> str:
    return plural(number, "фильм", "фильма", "фильмов")


def shows_count(number: int) -> str:
    return plural(number, "сеанс", "сеанса", "сеансов")


def days_count(number: int) -> str:
    return plural(number, "день", "дня", "дней")


def reply_keyboard() -> Dict[str, Any]:
    """Постоянные кнопки под полем ввода."""
    return {
        "keyboard": [
            [{"text": "🕷 Проверить Паука"}, {"text": "⚙️ Статус"}],
        ],
        "resize_keyboard": True,
    }


def verdict_keyboard(key: str, url: str) -> Dict[str, Any]:
    """Кнопки ручной проверки под карточкой фильма."""
    return {
        "inline_keyboard": [
            [{"text": "🔗 Открыть на сайте", "url": url}],
            [
                {"text": "✅ Это Паук", "callback_data": f"v:yes:{key}"},
                {"text": "❌ Не он", "callback_data": f"v:no:{key}"},
            ],
            [{"text": "🔕 Не писать про этот фильм", "callback_data": f"m:{key}"}],
        ]
    }


def _short_date(value: str) -> str:
    """«2026/08/05» в «05.08»."""
    parts = value.split("/")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else value


def _row(icon: str, label: str, value: str) -> str:
    return f"{icon} <b>{label}:</b> {value}"


def link(url: str, text: str = "Открыть на сайте") -> str:
    return f'🔗 <a href="{escape(url)}">{escape(text)}</a>'


def format_schedule(film: Film, limit: int = 12) -> str:
    """Расписание по дням: дата моноширинно, дальше времена через запятую."""
    if not film.shows:
        return ""

    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for show in film.shows:
        by_day.setdefault(show["day"], []).append(show)

    lines: List[str] = ["🕘 <b>Расписание</b>"]
    shown = 0
    for day, shows in list(by_day.items())[:5]:
        times: List[str] = []
        for show in shows:
            if shown >= limit:
                break
            mark = " (мест нет)" if show["sold_out"] else ""
            times.append(f"{escape(show['time'])}{mark}")
            shown += 1
        if times:
            lines.append(f"<code>{_short_date(day)}</code>  " + ", ".join(times))

    left = len(film.shows) - shown
    if left > 0:
        lines.append(f"и ещё {shows_count(left)}")
    return "\n".join(lines)


def format_card(
    film: Film,
    verdict: MatchResult = None,
    compact: bool = False,
    extra_block: str = "",
) -> str:
    """Карточка фильма. `extra_block` вставляется перед ссылкой."""
    title = f"🎬 <b>{escape(film.name)}</b>"
    if film.age_rest:
        title += f", {escape(film.age_rest)}+"
    lines: List[str] = [title, ""]

    if verdict is not None and verdict.level in CONFIDENCE_LABELS:
        lines.append(_row(CONFIDENCE_ICONS[verdict.level], "Уверенность", CONFIDENCE_LABELS[verdict.level]))

    lines.append(_row("🏢", "Кинотеатр", escape(film.site_name)))

    if film.show_count:
        # Слово «сеансов» уже стоит в подписи слева, поэтому здесь только число.
        tickets = str(film.show_count)
        if film.show_count_days:
            tickets += f" на {days_count(film.show_count_days)}"
        if film.min_price:
            tickets += f", от {film.min_price} ₽"
        lines.append(_row("🎟", "Сеансов", tickets))
    else:
        lines.append(_row("🎟", "Сеансов", "нет, билеты не продаются"))

    if film.on_screen:
        lines.append(_row("📆", "В прокате с", escape(film.on_screen)))
    elif film.status:
        lines.append(_row("📌", "Статус", escape(film.status_label)))

    if not compact:
        genre = ", ".join(film.genres[:4])
        details: List[str] = []
        if film.countries:
            details.append(", ".join(film.countries[:2]))
        if film.duration:
            details.append(f"{film.duration} мин")
        if genre or details:
            value = genre
            if details:
                value = f"{genre} ({', '.join(details)})" if genre else ", ".join(details)
            lines.append(_row("🎭", "Жанр", escape(value)))
        if film.director:
            lines.append(_row("🎥", "Режиссёр", escape(", ".join(film.director[:2]))))
        if film.cast:
            lines.append(_row("👥", "В ролях", escape(", ".join(film.cast[:4]))))

    if extra_block:
        lines.append("")
        lines.append(extra_block)

    if not compact and film.description:
        text = film.description[:400] + ("…" if len(film.description) > 400 else "")
        lines.append("")
        lines.append(f"<blockquote>{escape(text)}</blockquote>")

    lines.append("")
    lines.append(link(film.url))
    return "\n".join(lines)


def format_alert(change: str, film: Film, verdict: MatchResult, extra: str = "") -> str:
    if change == "tickets" and verdict.level in ("high", "medium"):
        # Главный сценарий, ради которого всё затевалось.
        header = (
            "🕷🎟🚨 БИЛЕТЫ НА ЧЕЛОВЕКА-ПАУКА В ПРОДАЖЕ"
            if verdict.level == "high"
            else "🕷🎟 Похоже, открылась продажа билетов на Паука"
        )
    else:
        header = CHANGE_TITLES.get(change, "ℹ️ Изменение в афише")
        if verdict.level == "high":
            header = f"🕷 Похоже, это Человек-Паук: Новый день\n{header}"

    parts = [f"<b>{escape(header)}</b>"]
    if extra:
        parts.append(escape(extra))

    schedule = format_schedule(film) if change in SHOW_CHANGES and verdict.is_candidate else ""
    parts.append(format_card(film, verdict, extra_block=schedule))
    return "\n\n".join(parts)


def format_reasons(verdict: MatchResult) -> str:
    """Разбор алгоритма. Показывается только в /candidates."""
    if not verdict.reasons:
        return ""
    lines = ["🔍 <b>Почему решил, что это он</b>"]
    for reason in verdict.reasons[:5]:
        text = reason[0].upper() + reason[1:] if reason else reason
        lines.append(f"• {escape(text)}")
    return "\n".join(lines)


def format_list(films_by_site: Dict[str, List[Film]]) -> str:
    lines: List[str] = ["🎬 <b>Текущая афиша</b>"]
    for site_name, films in films_by_site.items():
        lines.append("")
        lines.append(f"<b>{escape(site_name)}</b>, {films_count(len(films))}")
        for film in films:
            price = f", от {film.min_price} ₽" if film.min_price else ""
            lines.append(f"• {escape(film.name)}: {shows_count(film.show_count)}{price}")
    return "\n".join(lines)


def format_raw_hit(site_name: str, url: str, snippets: List[str]) -> str:
    lines = [
        "🕸 <b>Упоминание на сайте вне афиши</b>",
        "",
        _row("🏢", "Кинотеатр", escape(site_name)),
        "",
        "На странице встретились «паук» и «новый день» рядом. Возможно, анонс "
        "появился баннером или новостью раньше, чем расписанием.",
        "",
    ]
    for snippet in snippets[:3]:
        lines.append(f"<blockquote>…{escape(snippet[:250])}…</blockquote>")
    lines.append("")
    lines.append(link(url, "Открыть сайт"))
    return "\n".join(lines)
