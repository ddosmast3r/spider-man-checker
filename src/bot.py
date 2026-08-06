"""Телеграм-бот: команды, кнопки и фоновый цикл проверки сайтов."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from src import checker, health, notifier
from src.checker import CheckReport
from src.parser import Film
from src.storage import Storage
from src.telegram_api import Telegram, escape

log = logging.getLogger("bot")

HELP = """🕷 <b>Спайдер-чекер</b>

Слежу за одним фильмом, «Человек-Паук: Новый день», на двух сайтах: «Вершина»
и «Другар». Как только появятся билеты, пришлю расписание и цены.

Русское название может отличаться от оригинального, поэтому смотрю не только
заголовок, но и актёров, режиссёра, описание и жанр.

Про посторонние фильмы в чат не пишу. Изредка может прийти одна тихая строчка
«в афише появились новые фильмы, на цель не похожи»: это страховка на случай,
если название замаскируют до неузнаваемости. Отключается в .env параметром
NEW_FILM_DIGEST=0.

<b>Команды</b>
/check: проверить оба сайта прямо сейчас
/status: что известно про цель и когда была проверка
/health: состояние платы, температура, память, диск, IP и VPN
/candidates: подробные карточки совпадений
/list: вся афиша целиком
/watch слово: добавить своё ключевое слово
/unwatch слово: убрать ключевое слово
/keywords: список своих ключевых слов
/unmute: сбросить ручные вердикты
/stop: отписаться

<b>Когда пишу</b>
🎟 Билеты на цель поступили в продажу. Это главный сигнал.
➕ К цели добавили новые сеансы.
🆕 В афише появился похожий фильм, ещё до старта продаж.
✏️ Цель переименовали или сменился статус проката.
🕸 «Паук» и «новый день» встретились на странице вне расписания.

Под карточкой три кнопки: ✅ «Это Паук», ❌ «Не он», 🔕 «Не писать про этот фильм».
"""

COMMAND_LIST = [
    {"command": "check", "description": "Проверить Паука на обоих сайтах"},
    {"command": "candidates", "description": "Карточки найденных совпадений"},
    {"command": "list", "description": "Вся афиша целиком"},
    {"command": "keywords", "description": "Свои ключевые слова"},
    {"command": "status", "description": "Статистика и последняя проверка"},
    {"command": "health", "description": "Состояние платы: температура, память, VPN"},
    {"command": "help", "description": "Справка"},
    {"command": "stop", "description": "Отписаться"},
]

BUTTON_ALIASES = {
    "🕷 проверить паука": "/check",
    "проверить паука": "/check",
    "⚙️ статус": "/status",
    "статус": "/status",
    "🖥 состояние платы": "/health",
    "состояние платы": "/health",
}


class Bot:
    def __init__(self) -> None:
        self.tg = Telegram(config.TELEGRAM_TOKEN)
        self.store = Storage()
        self.last_films: Dict[str, List[Film]] = {}
        self.check_lock = threading.Lock()
        self.stop_event = threading.Event()

    # --- рассылка ---------------------------------------------------------
    def broadcast(self, text: str, keyboard: Optional[Dict[str, Any]] = None, loud: bool = True) -> None:
        for chat_id in self.store.subscribers:
            self.tg.send_message(chat_id, text, keyboard=keyboard, disable_notification=not loud)

    def notify_admin(self, text: str) -> None:
        if config.ADMIN_CHAT_ID:
            self.tg.send_message(config.ADMIN_CHAT_ID, text, disable_notification=True)
        else:
            self.broadcast(text, loud=False)

    # --- проверка ---------------------------------------------------------
    def do_check(self, requested_by: Optional[int] = None) -> CheckReport:
        with self.check_lock:
            report = checker.run_check(self.store)
        if report.films_by_site:
            self.last_films = report.films_by_site

        for alert in report.alerts:
            self.store.bump("alerts")
            self.broadcast(alert.text(), keyboard=alert.keyboard(), loud=alert.loud)
        for message in report.messages:
            self.broadcast(message, loud=True)
        for message in report.quiet_messages:
            self.broadcast(message, loud=False)

        if report.errors:
            self.notify_admin("⚠️ <b>Проблемы при проверке</b>\n• " + "\n• ".join(escape(e) for e in report.errors))

        if report.first_run_sites:
            self.broadcast(
                "✅ Первое сканирование завершено. Под наблюдением "
                f"<b>{notifier.films_count(report.total_films)}</b> "
                f"({escape(', '.join(report.first_run_sites))}).\n"
                "Дальше буду писать только про изменения.",
                loud=False,
            )

        if requested_by is not None and not report.alerts:
            self.tg.send_message(requested_by, self.target_summary(report))
        return report

    def target_summary(self, report: CheckReport) -> str:
        """Ответ на ручную проверку: что сейчас известно про цель."""
        when = datetime.now().strftime("%H:%M")
        found = checker.find_candidates(self.store, report.films_by_site)
        targets = [(f, v) for f, v in found if v.level in ("high", "medium")]

        sites = ", ".join(
            f"«{name}» {notifier.films_count(len(films))}"
            for name, films in report.films_by_site.items()
        )
        if not targets:
            return (
                "🕸 <b>Человека-Паука в афише нет</b>\n"
                f"Проверено в {when}. В репертуаре: {escape(sites)}.\n"
                "Слежу дальше, напишу сразу, как появятся билеты."
            )

        lines = [f"🕷 <b>Есть совпадения: {len(targets)}</b>", f"Проверено в {when}.", ""]
        for film, verdict in targets:
            tickets = (
                f"билеты есть, {notifier.shows_count(film.show_count)}"
                if film.show_count
                else "билетов пока нет"
            )
            lines.append(
                f"• <b>{escape(film.name)}</b>, {escape(film.site_name)}. "
                f"Уверенность {escape(notifier.CONFIDENCE_LABELS.get(verdict.level, ''))}, "
                f"{tickets}."
            )
        lines.append("")
        lines.append("Подробные карточки: /candidates")
        return "\n".join(lines)

    def checker_loop(self) -> None:
        # Небольшая пауза, чтобы бот успел ответить на /start.
        self.stop_event.wait(5)
        while not self.stop_event.is_set():
            delay = config.CHECK_INTERVAL
            try:
                report = self.do_check()
                # Цель уже в афише, переходим на частые проверки: билеты разбирают быстро.
                if report.hot:
                    delay = config.CHECK_INTERVAL_HOT
                    log.info("цель в афише (%s), проверяю каждые %s с", report.hot_targets, delay)
            except Exception as exc:  # цикл не должен умирать
                log.exception("ошибка цикла проверки")
                self.notify_admin(f"⚠️ <b>Сбой проверки</b>\n{escape(str(exc))}")
            self.stop_event.wait(delay)

    # --- команды -----------------------------------------------------------
    def ensure_films(self) -> Dict[str, List[Film]]:
        if not self.last_films:
            report = checker.run_check(self.store)
            if report.films_by_site:
                self.last_films = report.films_by_site
        return self.last_films

    def cmd_start(self, chat_id: int) -> None:
        fresh = self.store.subscribe(chat_id)
        greeting = "🕷 Подписка включена.\n\n" if fresh else "Вы уже подписаны.\n\n"
        self.tg.send_message(chat_id, greeting + HELP, keyboard=notifier.reply_keyboard())

    def cmd_stop(self, chat_id: int) -> None:
        self.store.unsubscribe(chat_id)
        self.tg.send_message(chat_id, "🔕 Отписал. Чтобы вернуться, нажмите /start")

    def cmd_check(self, chat_id: int) -> None:
        self.tg.send_message(chat_id, "🔄 Проверяю оба сайта…")
        self.do_check(requested_by=chat_id)

    def cmd_list(self, chat_id: int) -> None:
        films = self.ensure_films()
        if not films:
            self.tg.send_message(chat_id, "Не удалось получить афишу. Попробуйте /check позже.")
            return
        self.tg.send_message(chat_id, notifier.format_list(films))

    def cmd_candidates(self, chat_id: int) -> None:
        films = self.ensure_films()
        found = checker.find_candidates(self.store, films)
        if not found:
            self.tg.send_message(
                chat_id,
                "🕸 <b>Совпадений нет</b>\nЧеловека-Паука в афише пока не появилось.\n"
                "Продолжаю следить.",
            )
            return
        self.tg.send_message(chat_id, f"🕷 <b>Найдено совпадений: {len(found)}</b>")
        for film, verdict in found[:10]:
            # Разбор алгоритма показываем только здесь, в обычный алерт он не идёт.
            self.tg.send_message(
                chat_id,
                notifier.format_card(film, verdict, extra_block=notifier.format_reasons(verdict)),
                keyboard=notifier.verdict_keyboard(film.key, film.url),
            )

    def cmd_watch(self, chat_id: int, args: str) -> None:
        word = args.strip()
        if not word:
            self.tg.send_message(chat_id, "Укажите слово: <code>/watch новый день</code>")
            return
        added = self.store.add_keyword(word)
        self.tg.send_message(
            chat_id,
            f"{'✅ Добавлено' if added else 'Уже есть'}: <b>{escape(word)}</b>\n"
            f"Сейчас в списке: {escape(', '.join(self.store.keywords) or 'пусто')}",
        )

    def cmd_unwatch(self, chat_id: int, args: str) -> None:
        removed = self.store.remove_keyword(args.strip())
        self.tg.send_message(chat_id, "🗑 Убрано." if removed else "Такого слова нет в списке.")

    def cmd_keywords(self, chat_id: int) -> None:
        words = self.store.keywords
        self.tg.send_message(
            chat_id,
            "🔎 <b>Свои ключевые слова</b>\n" + ("\n".join(f"• {escape(w)}" for w in words) or "пока пусто")
            + "\n\nБазовые признаки (паук, «новый день», Том Холланд, Креттон, Marvel…) "
              "зашиты в алгоритм и работают всегда.",
        )

    def cmd_unmute(self, chat_id: int) -> None:
        self.store.state["muted"] = []
        self.store.state["verdicts"] = {}
        self.store.save()
        self.tg.send_message(chat_id, "🔔 Все ручные вердикты сброшены.")

    def cmd_status(self, chat_id: int) -> None:
        state = self.store.state
        last = state.get("last_check") or 0
        last_text = datetime.fromtimestamp(last).strftime("%d.%m.%Y %H:%M:%S") if last else "ещё не было"
        stats = state.get("stats", {})
        watched = sum(len(v) for v in state.get("events", {}).values())

        targets = [
            (film, verdict)
            for film, verdict in checker.find_candidates(self.store, self.last_films)
            if verdict.level in ("high", "medium")
        ]
        if targets:
            target_value = ", ".join(
                f"«{escape(f.name)}» ({escape(f.site_name)}, "
                f"{'билеты есть' if f.show_count else 'билетов нет'})"
                for f, _ in targets
            )
        else:
            target_value = "в афише пока нет"

        sites = ", ".join(s["name"] for s in config.SITES.values())
        lines = [
            "⚙️ <b>Статус</b>",
            "",
            f"🕷 <b>Человек-Паук:</b> {target_value}",
            f"🕒 <b>Последняя проверка:</b> {last_text}",
            f"⏱ <b>Интервал:</b> каждые {config.CHECK_INTERVAL // 60} мин, "
            f"а когда цель в афише каждые {config.CHECK_INTERVAL_HOT // 60} мин",
            f"🎬 <b>Под наблюдением:</b> {notifier.films_count(watched)}",
            f"🏢 <b>Площадки:</b> {escape(sites)}",
            f"👥 <b>Подписчиков:</b> {len(state.get('subscribers', []))}",
            f"📊 <b>Проверок:</b> {stats.get('checks', 0)}, "
            f"уведомлений {stats.get('alerts', 0)}, ошибок {stats.get('errors', 0)}",
        ]
        if state.get("last_error"):
            lines.append(f"⚠️ <b>Последняя ошибка:</b> {escape(state['last_error'])}")
        self.tg.send_message(chat_id, "\n".join(lines), keyboard=notifier.reply_keyboard())

    def cmd_health(self, chat_id: int) -> None:
        """Состояние машины, на которой крутится бот."""
        try:
            data = health.snapshot()
        except Exception as exc:  # метрики не должны ронять бота
            log.exception("не удалось собрать метрики")
            self.tg.send_message(
                chat_id,
                f"⚠️ Не удалось собрать состояние: {escape(str(exc))}",
                keyboard=notifier.reply_keyboard(),
            )
            return
        self.tg.send_message(chat_id, notifier.format_health(data),
                             keyboard=notifier.reply_keyboard())

    # --- маршрутизация ------------------------------------------------------
    def handle_message(self, message: Dict[str, Any]) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if not text:
            return
        lowered = text.lower()
        if lowered in BUTTON_ALIASES:
            text = BUTTON_ALIASES[lowered]
        command, _, args = text.partition(" ")
        command = command.split("@")[0].lower()

        if command not in ("/start", "/help") and chat_id not in self.store.subscribers:
            self.store.subscribe(chat_id)

        handlers = {
            "/start": lambda: self.cmd_start(chat_id),
            "/help": lambda: self.tg.send_message(chat_id, HELP, keyboard=notifier.reply_keyboard()),
            "/check": lambda: self.cmd_check(chat_id),
            "/list": lambda: self.cmd_list(chat_id),
            "/candidates": lambda: self.cmd_candidates(chat_id),
            "/watch": lambda: self.cmd_watch(chat_id, args),
            "/unwatch": lambda: self.cmd_unwatch(chat_id, args),
            "/keywords": lambda: self.cmd_keywords(chat_id),
            "/unmute": lambda: self.cmd_unmute(chat_id),
            "/status": lambda: self.cmd_status(chat_id),
            "/health": lambda: self.cmd_health(chat_id),
            "/stop": lambda: self.cmd_stop(chat_id),
        }
        handler = handlers.get(command)
        if handler:
            handler()
        else:
            self.tg.send_message(chat_id, "Не понял команду. Список команд: /help",
                                 keyboard=notifier.reply_keyboard())

    def handle_callback(self, callback: Dict[str, Any]) -> None:
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        if data.startswith("v:"):
            _, verdict, key = data.split(":", 2)
            self.store.set_verdict(key, verdict)
            if verdict == "yes":
                self.tg.answer_callback(callback["id"], "✅ Отмечено как Человек-Паук", alert=True)
                self.broadcast(
                    "🕷🚨 <b>Подтверждено вручную: цель найдена</b>\n"
                    "Теперь буду сообщать про любые изменения по этому фильму.",
                )
            else:
                self.tg.answer_callback(callback["id"], "❌ Отмечено: это не он")
        elif data.startswith("m:"):
            key = data.split(":", 1)[1]
            self.store.mute(key)
            self.tg.answer_callback(callback["id"], "🔕 Больше не пишу про этот фильм")
        else:
            self.tg.answer_callback(callback["id"])
            return

        if chat_id and message_id:
            self.tg.edit_markup(chat_id, message_id, None)

    # --- главный цикл --------------------------------------------------------
    def run(self) -> None:
        me = self.tg.get_me()
        log.info("бот запущен: @%s", me.get("username"))
        self.tg.set_commands(COMMAND_LIST)

        thread = threading.Thread(target=self.checker_loop, name="checker", daemon=True)
        thread.start()

        offset = 0
        while not self.stop_event.is_set():
            try:
                updates = self.tg.get_updates(offset, timeout=30)
            except Exception as exc:
                log.warning("getUpdates: %s", exc)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        self.handle_message(update["message"])
                    elif "callback_query" in update:
                        self.handle_callback(update["callback_query"])
                except Exception:
                    log.exception("ошибка обработки апдейта")
