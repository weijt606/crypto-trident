"""Shared plumbing for the live executors: atomic state files, the single-instance
lock, deterministic client order ids, fill resolution, key-permission checks,
startup auth-failure counting and secret redaction of exchange error text.

Nothing here makes trading decisions; it only keeps the executors' I/O honest.
"""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from execution.halt import TraderHalted

try:  # POSIX only; on platforms without fcntl the lock degrades to a no-op with a warning.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# ---- atomic writes ----


def atomic_write_text(path: Path, text: str) -> None:
    """Write via tmp file + fsync + os.replace, so a crash never leaves a torn file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:  # persist the rename itself
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False))


# ---- single-instance lock ----


class InstanceLock:
    """Non-blocking exclusive flock on <live_dir>/<name>.lock.

    Two processes managing the same bot would double-trade and corrupt the state file.
    The lock is released by close() or automatically when the process exits."""

    def __init__(self, live_dir: Path, name: str):
        self.path = Path(live_dir) / f"{name}.lock"
        self._f = open(self.path, "a+")
        if fcntl is None:  # pragma: no cover
            return
        try:
            fcntl.flock(self._f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self._f.close()
            self._f = None
            if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise TraderHalted(
                    f"{name}: another process already holds {self.path} (is the live "
                    "executor or a retire script still running?). Stop it first; two "
                    "processes must never manage the same bot.") from e
            raise
        self._f.seek(0)
        self._f.truncate()
        self._f.write(f"{os.getpid()}\n")
        self._f.flush()

    def close(self) -> None:
        f, self._f = self._f, None
        if f is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            finally:
                f.close()


# ---- client order ids ----

_CID_BAD = re.compile(r"[^.A-Za-z0-9:/_-]")
CID_MAX_LEN = 36  # Binance newClientOrderId: ^[.A-Z:/a-z0-9_-]{1,36}$


def client_order_id(bot: str, bar_ts: int, tag: str | int) -> str:
    """Deterministic per-order id: f"{bot}-{bar_ts}-{tag}", sanitized to Binance's rule.

    Orders are never re-sent for a decided bar; a timed-out send is resolved by client-id
    lookup (the same (bot, bar, tag) always yields the same id). Callers that may send a
    new order on an already-used bar (flatten re-runs) must put a nonce in `tag`. When the
    bot name must be shortened, a hash of the full name keeps ids distinct per bot."""
    suffix = _CID_BAD.sub("_", f"-{int(bar_ts)}-{tag}")
    name = _CID_BAD.sub("_", bot)
    room = CID_MAX_LEN - len(suffix)
    if len(name) > room:
        h = hashlib.sha1(bot.encode()).hexdigest()[:6]
        name = name[:max(0, room - 7)] + "." + h if room >= 7 else h[:max(1, room)]
    return (name + suffix)[:CID_MAX_LEN]


def find_order_by_client_id(exchange, symbol: str, cid: str) -> dict | None:
    """Look up an order by client id after an ambiguous send (timeout / network error).
    Returns the order, or None if the exchange has no such order (or lookup failed)."""
    fetch = getattr(exchange, "fetch_order", None)
    if not callable(fetch):
        return None
    try:
        return fetch(None, symbol, {"origClientOrderId": cid})
    except Exception:  # noqa: BLE001  OrderNotFound or lookup failure: treat as not placed
        return None


def _filled_avg(resp: dict | None) -> tuple[float, float]:
    if not resp:
        return 0.0, 0.0
    try:
        filled = float(resp.get("filled") or 0.0)
    except (TypeError, ValueError):
        filled = 0.0
    try:
        avg = float(resp.get("average") or 0.0)
    except (TypeError, ValueError):
        avg = 0.0
    if not avg and filled:
        try:
            cost = float(resp.get("cost") or 0.0)
            avg = cost / filled if cost else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            avg = 0.0
    if not (math.isfinite(filled) and math.isfinite(avg)):
        return 0.0, 0.0
    return filled, avg


_OPEN_STATUSES = {"open", "new", "partially_filled"}


def resolve_fill(exchange, symbol: str, resp: dict | None, cid: str,
                 attempts: int = 3, poll_s: float = 0.5) -> tuple[dict | None, float, float]:
    """Return (order, filled, average) as the exchange actually executed it.

    A market order response can report filled=0 / status NEW (async ack) even though it
    fills a moment later. In that case re-read the order (by id, else by client id) a few
    times instead of trusting the ack."""
    filled, avg = _filled_avg(resp)
    status = str((resp or {}).get("status") or "").lower()
    if filled > 0 and avg > 0 and status not in _OPEN_STATUSES:
        return resp, filled, avg
    fetch = getattr(exchange, "fetch_order", None)
    if not callable(fetch):
        return resp, filled, avg
    oid = (resp or {}).get("id")
    order = resp
    for i in range(max(1, attempts)):
        try:
            order = (fetch(oid, symbol) if oid
                     else fetch(None, symbol, {"origClientOrderId": cid}))
        except Exception:  # noqa: BLE001
            order = None
        f2, a2 = _filled_avg(order)
        st = str((order or {}).get("status") or "").lower()
        if f2 > 0 and a2 > 0:
            filled, avg = f2, a2
            if st not in _OPEN_STATUSES:
                break
        if i + 1 < attempts and poll_s > 0:
            time.sleep(poll_s)
    return order or resp, filled, avg


# ---- key permission checks ----

def key_restriction_problems(r: dict, need: str) -> list[str]:
    """Hard failures for a trade key's API restrictions (same rules as
    scripts/verify_trade_key.py). need = "spot" or "futures"."""
    def b(field: str) -> bool:
        return str(r.get(field)).lower() == "true"

    problems = []
    if b("enableWithdrawals"):
        problems.append("key has withdrawals enabled (must never be enabled)")
    if b("permitsUniversalTransfer"):
        problems.append("key has universal transfer enabled (breaks fund isolation)")
    if b("enableInternalTransfer"):
        problems.append("key has internal transfer enabled (breaks fund isolation)")
    if b("enableMargin"):
        problems.append("key has margin borrowing enabled")
    if b("enableVanillaOptions"):
        problems.append("key has options trading enabled")
    if need == "spot" and not b("enableSpotAndMarginTrading"):
        problems.append("key lacks spot trading permission")
    if need == "futures" and not b("enableFutures"):
        problems.append("key lacks futures permission")
    return problems


# ---- startup auth-failure counter ----

STARTUP_AUTH_FAIL_LIMIT = 5


def record_startup_auth_failure(live_dir: Path, name: str,
                                limit: int = STARTUP_AUTH_FAIL_LIMIT) -> int:
    """Count consecutive startup auth failures in <live_dir>/<name>.auth_fails.
    Returns the new count; at `limit` the caller should halt (exit 78) instead of
    letting the service manager restart-loop against the exchange."""
    path = Path(live_dir) / f"{name}.auth_fails"
    try:
        n = int(path.read_text().strip() or 0)
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        atomic_write_text(path, f"{n}\n")
    except OSError:
        pass
    return n


def reset_startup_auth_failures(live_dir: Path, name: str) -> None:
    try:
        (Path(live_dir) / f"{name}.auth_fails").unlink()
    except FileNotFoundError:
        pass


# ---- redaction ----

_SIG_RE = re.compile(r"(signature|apiKey|X-MBX-APIKEY)=?[\"':\s]*[A-Za-z0-9]{16,}", re.IGNORECASE)
_QUERY_RE = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*")


def redact(text: object) -> str:
    """Strip URL query strings (they carry timestamp/signature) and any signature/api
    key values from exchange error text before it goes to logs, events or alerts."""
    s = str(text)
    s = _QUERY_RE.sub(r"\1?<redacted>", s)
    s = _SIG_RE.sub(lambda m: f"{m.group(1)}=<redacted>", s)
    return s
