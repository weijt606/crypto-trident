"""Alerts: persisted locally (read by the dashboard) plus an optional Telegram push.

Every alert is first appended to storage/alerts.jsonl, which the dashboard's
/api/alerts endpoint reads. This file is the source of truth for web alerts, so
it must not depend on any push channel being configured. The file is rotated
to alerts.jsonl.1 once it exceeds MAX_ALERTS_BYTES (one backup is kept)."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

from core.config import env
from monitor.logger import get_logger

log = get_logger("alerts")

MAX_ALERTS_BYTES = 5 * 1024 * 1024
_TOKEN_RE = re.compile(r"^\d+:[\w-]+$")
_CHAT_ID_RE = re.compile(r"^(-?\d+|@[A-Za-z][A-Za-z0-9_]{4,})$")


def valid_telegram_config(token: str, chat_id: str) -> bool:
    """Bot token must look like <digits>:<token chars>; chat id must be a
    (possibly negative) integer or an @channel username. Checked before the
    token is put into a URL so a malformed value cannot alter the request."""
    return bool(_TOKEN_RE.fullmatch(token or "")) and bool(_CHAT_ID_RE.fullmatch(chat_id or ""))


def _rotate_if_needed(path, max_bytes: int = MAX_ALERTS_BYTES) -> None:
    """Move path to path.1 (replacing an older backup) once it exceeds max_bytes."""
    try:
        if path.stat().st_size > max_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass


def _level(text: str) -> str:
    """Coarse severity from the message prefix, used for colouring/sorting."""
    head = text.lstrip()
    if head.startswith("[CRITICAL]"):
        return "critical"
    if head.startswith("[WARNING]"):
        return "warning"
    return "info"


def _persist(text: str, level: str) -> None:
    """Append the alert to storage/alerts.jsonl. Failures must never break the trading loop."""
    try:
        from core.config import storage_dir

        rec = {"ts": int(time.time() * 1000), "level": level, "text": text}
        path = storage_dir() / "alerts.jsonl"
        _rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log.error("failed to persist alert: %s", e)


def send_alert(text: str) -> bool:
    """Persist the alert, then try Telegram if configured.

    Telegram is enabled only when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set
    in the environment or .env. Returns True only if the push was actually delivered."""
    from execution.common import redact

    text = redact(text)
    _persist(text, _level(text))
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("[no push channel configured, alert logged only] %s", text)
        return False
    token, chat_id = token.strip(), chat_id.strip()
    if not valid_telegram_config(token, chat_id):
        # never log the token itself
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID malformed; push skipped, alert logged only")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = json.load(resp).get("ok", False)
        if not ok:
            log.error("Telegram returned failure: %s", text)
        return ok
    except Exception as e:  # alert failures must never break the trading loop
        # str(e) of a URL error can embed the request URL, which contains the token
        log.error("Telegram send error (%s): %s", type(e).__name__, text)
        return False
