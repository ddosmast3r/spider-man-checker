"""Логика проверки: забрать афишу, сравнить со слепком, собрать алерты."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config
from src import matcher, notifier
from src.matcher import MatchResult
from src.parser import Film, ParseError, fetch_site
from src.storage import Storage

log = logging.getLogger("checker")

# Изменения, о которых пишем всегда (со звуком).
LOUD_CHANGES = {"new", "tickets", "on_sale", "renamed", "more_shows"}


@dataclass
class Alert:
    change: str
    film: Film
    verdict: MatchResult
    extra: str = ""

    @property
    def loud(self) -> bool:
        return self.verdict.level in ("high", "medium") or self.change in LOUD_CHANGES

    def text(self) -> str:
        return notifier.format_alert(self.change, self.film, self.verdict, self.extra)

    def keyboard(self) -> Dict:
        return notifier.verdict_keyboard(self.film.key, self.film.url)


@dataclass
class CheckReport:
    alerts: List[Alert] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)        # со звуком
    quiet_messages: List[str] = field(default_factory=list)  # без звука
    films_by_site: Dict[str, List[Film]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    first_run_sites: List[str] = field(default_factory=list)
    hot_targets: List[str] = field(default_factory=list)

    @property
    def total_films(self) -> int:
        return sum(len(films) for films in self.films_by_site.values())

    @property
    def has_news(self) -> bool:
        return bool(self.alerts or self.messages or self.quiet_messages)

    @property
    def hot(self) -> bool:
        """Цель уже в афише — значит, проверяем чаще, чтобы не проспать билеты."""
        return bool(self.hot_targets)


def _digest(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def check_site(site_key: str, store: Storage, report: CheckReport) -> None:
    site_name = config.SITES[site_key]["name"]
    try:
        snapshot = fetch_site(site_key)
    except ParseError as exc:
        report.errors.append(f"{site_name}: не удалось разобрать страницу. {exc}")
        return
    except Exception as exc:  # сеть, 5xx, таймауты
        report.errors.append(f"{site_name}: {type(exc).__name__}, {exc}")
        return

    report.films_by_site[site_name] = snapshot.films
    known = store.site_events(site_key)
    first_run = not store.is_initialized(site_key)
    if first_run:
        report.first_run_sites.append(site_name)

    keywords = store.keywords
    seen: set = set()
    newcomers: List[Film] = []  # новые посторонние фильмы — только для тихой сводки

    for film in snapshot.films:
        seen.add(film.uuid)
        verdict = matcher.match(film, keywords)
        # Ручной вердикт пользователя весомее алгоритма.
        manual = store.verdict(film.key)
        if manual == "yes" and verdict.level != "high":
            verdict = MatchResult("high", 99, ["Подтверждено вручную"])

        is_target = verdict.level in ("high", "medium")
        if verdict.level == "high" and not store.is_muted(film.key):
            report.hot_targets.append(f"{site_name}: {film.name}")
        previous = known.get(film.uuid)

        # Что изменилось у фильма с прошлой проверки.
        changes: List[str] = []
        if previous is None:
            changes.append("new")
        else:
            if (previous.get("name") or "") != film.name:
                changes.append("renamed")
            if (previous.get("status") or "") != film.status:
                changes.append("status")

        # Главное событие: у цели появились билеты.
        tickets_change = ""
        if is_target:
            if film.show_count > 0 and not store.tickets_notified(film.key):
                tickets_change = "tickets"
            elif (
                film.show_count > 0
                and verdict.level == "high"
                and previous
                and set(film.show_ids) - set(previous.get("show_ids") or [])
            ):
                tickets_change = "more_shows"
        if film.show_count == 0:
            # Билеты пропали — при следующем появлении сообщим заново.
            store.clear_tickets(film.key)

        known[film.uuid] = film.snapshot()

        if store.is_muted(film.key):
            continue

        # Посторонние фильмы в чат не идут: максимум строчка в тихой сводке.
        if not is_target:
            if "new" in changes and not first_run and config.NEW_FILM_DIGEST:
                newcomers.append(film)
            continue

        if first_run and not tickets_change:
            report.alerts.append(
                Alert("new", film, verdict, extra="Найден уже при первом сканировании.")
            )
            continue

        if tickets_change:
            store.mark_tickets(film.key)
            extra = (
                "Появились сеансы, билеты можно брать."
                if tickets_change == "tickets"
                else "К уже известным сеансам добавились новые."
            )
            report.alerts.append(Alert(tickets_change, film, verdict, extra))
            changes = [c for c in changes if c not in ("new", "on_sale")]

        if first_run:
            continue

        for change in changes:
            extra = ""
            if change == "renamed" and previous:
                extra = f"Было: «{previous.get('name')}» → стало: «{film.name}»"
            if change == "status" and previous:
                extra = f"Было: {previous.get('status') or 'не указан'}. Стало: {film.status or 'не указан'}"
            report.alerts.append(Alert(change, film, verdict, extra))

    if newcomers:
        names = ", ".join(f"«{notifier.escape(f.name)}»" for f in newcomers[:12])
        report.quiet_messages.append(
            f"В афише «{notifier.escape(site_name)}» появились новые фильмы "
            f"({len(newcomers)}), на цель не похожи: {names}."
        )

    # Пропавшие из афиши — сообщаем только про подтверждённых кандидатов.
    for uuid in list(known.keys()):
        if uuid in seen:
            continue
        snap = known.pop(uuid)
        key = f"{site_key}:{uuid}"
        if store.verdict(key) == "yes":
            report.messages.append(
                f"<b>{notifier.escape(snap.get('name', '?'))}</b> пропал из афиши "
                f"«{notifier.escape(site_name)}»."
            )

    # Запасной скан сырой страницы — на случай анонса вне расписания.
    for snippet in matcher.scan_raw_html(snapshot.html):
        digest = _digest(snippet)
        if store.seen_raw_hit(digest):
            continue
        store.add_raw_hit(digest)
        if not first_run:
            report.messages.append(
                notifier.format_raw_hit(site_name, config.SITES[site_key]["url"], [snippet])
            )

    store.mark_initialized(site_key)


def run_check(store: Storage) -> CheckReport:
    report = CheckReport()
    for site_key in config.SITES:
        check_site(site_key, store, report)
    store.bump("checks")
    if report.errors:
        store.bump("errors", len(report.errors))
    store.state["last_error"] = "; ".join(report.errors)
    store.state["last_check"] = int(time.time())
    store.save()
    log.info(
        "проверка завершена: %s фильмов, %s алертов, %s ошибок",
        report.total_films,
        len(report.alerts),
        len(report.errors),
    )
    return report


def find_candidates(store: Storage, films_by_site: Dict[str, List[Film]]) -> List[tuple]:
    """Все фильмы с ненулевой оценкой — для команды /candidates."""
    out = []
    for films in films_by_site.values():
        for film in films:
            verdict = matcher.match(film, store.keywords)
            if store.verdict(film.key) == "yes":
                verdict = MatchResult("high", 99, ["Подтверждено вручную"])
            if verdict.is_candidate:
                out.append((film, verdict))
    out.sort(key=lambda pair: -pair[1].score)
    return out
