"""Состояние машины, на которой запущен бот: температура, память, диск, сеть, VPN.

Модуль намеренно не требует прав root: всё берётся из /proc, /sys и обычных
системных вызовов. Там, где данных нет (например, бот запущен на macOS без
/sys/class/thermal), соответствующая строка просто не попадёт в отчёт.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

# Интерфейс туннеля AmneziaWG и его systemd-юнит.
VPN_INTERFACE = os.environ.get("VPN_INTERFACE", "awg0")
VPN_UNIT = os.environ.get("VPN_UNIT", "awg-quick@awg0")

# Сервисы, состояние которых показываем в отчёте.
WATCHED_UNITS = ("spider-checker", VPN_UNIT, "uptime-kuma")

# Куда ходим за внешним адресом. Сервис отвечает одной строкой с IP.
PUBLIC_IP_URL = "https://api.ipify.org"


def _read(path: str) -> Optional[str]:
    """Прочитать файл, вернуть None вместо исключения."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def cpu_temp() -> Optional[float]:
    """Температура процессора в градусах Цельсия."""
    zones = Path("/sys/class/thermal")
    if not zones.is_dir():
        return None
    for zone in sorted(zones.glob("thermal_zone*")):
        raw = _read(str(zone / "temp"))
        if raw and raw.lstrip("-").isdigit():
            value = int(raw)
            # Ядро отдаёт милли-градусы, но некоторые платы пишут сразу градусы.
            return value / 1000 if abs(value) > 1000 else float(value)
    return None


def memory() -> Optional[Dict[str, int]]:
    """Всего и доступно памяти в килобайтах."""
    raw = _read("/proc/meminfo")
    if not raw:
        return None
    values: Dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0])
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return {"total": total, "available": available, "used": total - available}


def uptime_seconds() -> Optional[float]:
    """Сколько машина работает без перезагрузки."""
    raw = _read("/proc/uptime")
    if raw:
        try:
            return float(raw.split()[0])
        except (ValueError, IndexError):
            return None
    return None


def load_average() -> Optional[tuple]:
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return None


def disk_usage(path: str = "/") -> Optional[Dict[str, int]]:
    """Занятое и свободное место в байтах."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def local_ip() -> Optional[str]:
    """Адрес в локальной сети. UDP-сокет никуда не шлёт, только выбирает маршрут."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def public_ip(timeout: int = 6) -> Optional[str]:
    """Внешний адрес. Запрос идёт мимо туннеля, поэтому это адрес провайдера."""
    try:
        response = requests.get(PUBLIC_IP_URL, timeout=timeout)
        if response.ok:
            value = response.text.strip()
            return value if len(value) <= 45 else None
    except requests.RequestException:
        return None
    return None


def unit_state(unit: str) -> str:
    """Состояние systemd-юнита. Прав root не требует."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
        return (result.stdout or result.stderr).strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def vpn_state() -> Dict[str, object]:
    """Состояние туннеля.

    Рукопожатие через `awg show` требует прав root, поэтому судим по трём
    доступным признакам: интерфейс поднят, юнит активен, Telegram отвечает.
    Последний признак и есть то, ради чего туннель нужен.
    """
    iface_path = Path(f"/sys/class/net/{VPN_INTERFACE}")
    up = iface_path.is_dir()
    state = {
        "interface": VPN_INTERFACE,
        "up": up,
        "unit": unit_state(VPN_UNIT),
        "rx": None,
        "tx": None,
        "telegram": False,
    }
    if up:
        rx = _read(f"/sys/class/net/{VPN_INTERFACE}/statistics/rx_bytes")
        tx = _read(f"/sys/class/net/{VPN_INTERFACE}/statistics/tx_bytes")
        state["rx"] = int(rx) if rx and rx.isdigit() else None
        state["tx"] = int(tx) if tx and tx.isdigit() else None
    try:
        response = requests.get("https://api.telegram.org", timeout=8)
        state["telegram"] = response.status_code < 500
    except requests.RequestException:
        state["telegram"] = False
    return state


def human_bytes(value: Optional[int]) -> str:
    if value is None:
        return "?"
    step = 1024.0
    amount = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if amount < step:
            return f"{amount:.0f} {unit}" if unit == "Б" else f"{amount:.1f} {unit}"
        amount /= step
    return f"{amount:.1f} ПБ"


def human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def snapshot() -> Dict[str, object]:
    """Собрать все метрики разом."""
    return {
        "hostname": socket.gethostname(),
        "temp": cpu_temp(),
        "memory": memory(),
        "disk": disk_usage("/"),
        "uptime": uptime_seconds(),
        "load": load_average(),
        "local_ip": local_ip(),
        "public_ip": public_ip(),
        "vpn": vpn_state(),
        "units": {unit: unit_state(unit) for unit in WATCHED_UNITS},
        "collected_at": time.time(),
    }
