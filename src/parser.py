"""Парсер афиши кинотеатров на платформе p24.app.

Оба сайта (kino-vershina.ru и drugar.ru) сделаны на Next.js App Router. Вся афиша
приезжает сервером внутри RSC-пейлоада (`self.__next_f.push([1,"..."])`)
готовым JSON-ом, поэтому парсим не HTML-вёрстку (она меняется от релиза
к релизу), а сам пейлоад, что на порядок стабильнее.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

import config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.S)

STATUS_LABELS = {
    "rental": "в прокате",
    "soon": "скоро",
    "preorder": "предпродажа",
    "archive": "архив",
}


class ParseError(RuntimeError):
    """Не удалось разобрать страницу, вероятно сайт переехал на новый формат."""


@dataclass
class Film:
    site: str
    site_name: str
    uuid: str
    href: str
    url: str
    name: str
    status: str
    kind: str
    show_count: int
    show_count_today: int
    show_count_days: int
    on_screen: str
    date_start: str
    age_rest: str
    duration: int
    description: str
    genres: List[str] = field(default_factory=list)
    countries: List[str] = field(default_factory=list)
    cast: List[str] = field(default_factory=list)
    director: List[str] = field(default_factory=list)
    producer: List[str] = field(default_factory=list)
    poster: str = ""
    dates: List[str] = field(default_factory=list)
    formats: List[str] = field(default_factory=list)
    min_price: Optional[int] = None
    shows: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.site}:{self.uuid}"

    @property
    def on_sale(self) -> bool:
        return self.show_count > 0

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status or "не указан")

    @property
    def available_shows(self) -> List[Dict[str, Any]]:
        """Сеансы, на которые ещё можно купить билет."""
        return [s for s in self.shows if not s.get("sold_out")]

    @property
    def show_ids(self) -> List[str]:
        return sorted(s.get("id", "") for s in self.shows)

    def day_url(self, date: str) -> str:
        """Ссылка на страницу фильма с выбранной датой."""
        return f"{self.url}?date={date}" if date else self.url

    def snapshot(self) -> Dict[str, Any]:
        """Слепок полей, по изменению которых бот принимает решение об алерте."""
        return {
            "uuid": self.uuid,
            "href": self.href,
            "name": self.name,
            "status": self.status,
            "show_count": self.show_count,
            "dates": self.dates,
            "min_price": self.min_price,
            "on_screen": self.on_screen,
            "url": self.url,
            "show_ids": self.show_ids,
            "available": len(self.available_shows),
        }

    def searchable_text(self) -> str:
        """Всё текстовое содержимое карточки, для алгоритма распознавания."""
        chunks = [
            self.name,
            self.href,
            self.description,
            " ".join(self.genres),
            " ".join(self.countries),
            " ".join(self.cast),
            " ".join(self.director),
            " ".join(self.producer),
        ]
        return " ".join(c for c in chunks if c)


def _balanced(text: str, start: int) -> Optional[str]:
    """Вырезает сбалансированный JSON-фрагмент, начиная с '[' или '{'."""
    open_c = text[start]
    close_c = "]" if open_c == "[" else "}"
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def fetch_html(url: str, timeout: int = None) -> str:
    timeout = timeout or config.HTTP_TIMEOUT
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    response.raise_for_status()
    return response.text


def decode_flight(html: str) -> str:
    """Склеивает RSC-пейлоад Next.js в одну строку."""
    parts = FLIGHT_RE.findall(html)
    if not parts:
        raise ParseError("на странице нет RSC-пейлоада (__next_f)")
    out = []
    for part in parts:
        try:
            out.append(json.loads(part))
        except json.JSONDecodeError:
            continue
    flight = "".join(out)
    if not flight:
        raise ParseError("RSC-пейлоад не декодировался")
    return flight


def extract_raw_events(flight: str) -> List[Dict[str, Any]]:
    """Достаёт все объекты событий из пейлоада, схлопывая дубли по uuid."""
    found: Dict[str, Dict[str, Any]] = {}
    for match in re.finditer(r'"events":\[', flight):
        raw = _balanced(flight, match.end() - 1)
        if not raw:
            continue
        try:
            events = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for event in events:
            if isinstance(event, dict) and event.get("uuid") and "info" in event:
                found[event["uuid"]] = event
    return list(found.values())


def _collect_shows(schedule: Any) -> List[Dict[str, Any]]:
    """Вытаскивает плоский список сеансов из вложенной структуры дата→город→площадка→зал."""
    shows: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and "date" in item:
                    shows.append(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(schedule)
    return shows


def normalize_event(raw: Dict[str, Any], site_key: str) -> Film:
    site = config.SITES[site_key]
    info = raw.get("info") or {}
    filters = raw.get("filters") or {}

    dates: List[str] = []
    for city_dates in (filters.get("dates") or {}).values():
        if isinstance(city_dates, list):
            dates.extend(city_dates)
    dates = sorted(set(dates))

    prices: List[int] = []
    shows: List[Dict[str, Any]] = []
    for show in _collect_shows(raw.get("schedule")):
        show_prices = [
            int(p["price"])
            for p in (show.get("prices") or [])
            if isinstance(p.get("price"), (int, float))
        ]
        prices.extend(show_prices)
        when = str(show.get("date") or "")
        day, _, clock = when.partition(" ")
        shows.append(
            {
                "id": show.get("uuid") or show.get("showId") or when,
                "day": day,
                "time": clock,
                "sort": show.get("realDate") or 0,
                "price": min(show_prices) if show_prices else None,
                "hall": str(show.get("hall") or ""),
                "formats": list(show.get("formats") or []),
                "vip": bool(show.get("isVip")),
                "sold_out": bool(show.get("isNoFreeSeats")),
            }
        )
    shows.sort(key=lambda s: (s["sort"], s["day"], s["time"]))

    href = raw.get("href") or ""
    return Film(
        site=site_key,
        site_name=site["name"],
        uuid=raw.get("uuid", ""),
        href=href,
        url=site["event_url"].format(href=href) if href else site["url"],
        name=(info.get("name") or "").strip(),
        status=raw.get("status") or "",
        kind=raw.get("type") or "",
        show_count=int(raw.get("showCount") or 0),
        show_count_today=int(raw.get("showCountToday") or 0),
        show_count_days=int(raw.get("showCountDays") or 0),
        on_screen=info.get("onScreen") or "",
        date_start=info.get("dateStart") or "",
        age_rest=str(info.get("ageRest") or ""),
        duration=int(info.get("duration") or 0),
        description=(info.get("description") or "").strip(),
        genres=list(info.get("genre") or []),
        countries=list(info.get("country") or []),
        cast=list(info.get("cast") or []),
        director=list(info.get("director") or []),
        producer=list(info.get("producer") or []),
        poster=info.get("poster") or "",
        dates=dates,
        shows=shows,
        formats=list(filters.get("formats") or []),
        min_price=min(prices) if prices else None,
    )


def fetch_site(site_key: str) -> "SiteSnapshot":
    """Забирает афишу одной площадки. Возвращает фильмы + сырой HTML для запасного скана."""
    site = config.SITES[site_key]
    html = fetch_html(site["url"])
    flight = decode_flight(html)
    raw_events = extract_raw_events(flight)
    if not raw_events:
        raise ParseError(f"{site['name']}: в пейлоаде не найдено ни одного фильма")
    films = [normalize_event(raw, site_key) for raw in raw_events]
    films = [f for f in films if f.name]
    films.sort(key=lambda f: (-f.show_count, f.name))
    return SiteSnapshot(site_key=site_key, site_name=site["name"], films=films, html=html, flight=flight)


@dataclass
class SiteSnapshot:
    site_key: str
    site_name: str
    films: List[Film]
    html: str
    flight: str
