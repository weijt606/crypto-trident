"""Config loading: config/system.yaml + config/bots/*.yaml + .env (secrets).

.env is never exported into os.environ: secrets are read on demand through
env() / read_only_keys() / trade_keys(), so child processes and unrelated
libraries do not inherit them. A variable already set in the process
environment (e.g. injected by a container runtime) takes precedence over .env.
"""
from __future__ import annotations

import os
import re
import stat
import warnings
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = PROJECT_ROOT / ".env"

_perm_warned = False
_redaction_values: set[str] = set()
_CREDENTIAL_NAMES = frozenset({
    "BINANCE_READ_API_KEY", "BINANCE_READ_API_SECRET", "BINANCE_TRADE_API_KEY",
    "BINANCE_TRADE_API_SECRET", "TELEGRAM_BOT_TOKEN",
})


def _dotenv() -> dict[str, str | None]:
    global _perm_warned
    if not ENV_FILE.exists():
        return {}
    if not _perm_warned:
        _perm_warned = True
        try:
            if ENV_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                warnings.warn(f"{ENV_FILE} is readable by group/others; run: chmod 600 .env",
                              stacklevel=3)
        except OSError:
            pass
    return dotenv_values(ENV_FILE)


def env(name: str, default: str | None = None) -> str | None:
    """Look up a setting: process environment first, then .env, then default.
    Empty values count as unset."""
    val = os.environ.get(name)
    if not val:
        val = _dotenv().get(name)
    if name in _CREDENTIAL_NAMES and val and len(val) >= 8:
        _redaction_values.add(val)
    return val if val else default


def load_system_config() -> dict[str, Any]:
    with open(CONFIG_DIR / "system.yaml") as f:
        return yaml.safe_load(f)


BOT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_bot_name(name: str) -> str:
    """Bot names become file names (state, lock, kill markers) and client order ids:
    only [A-Za-z0-9_.-] is allowed, and never a bare '.' or '..'."""
    if not isinstance(name, str) or not BOT_NAME_RE.fullmatch(name) or name in (".", ".."):
        raise ValueError(f"invalid bot name {name!r}: use only letters, digits, '_', '.', '-'")
    return name


def load_bot_config(name: str) -> dict[str, Any]:
    validate_bot_name(name)
    path = CONFIG_DIR / "bots" / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in (CONFIG_DIR / "bots").glob("*.yaml"))
        raise FileNotFoundError(f"bot config {name!r} not found; available: {available}")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path.name}: bot config must be a mapping")
    if cfg.get("name") != name:
        raise ValueError(f"{path.name}: name {cfg.get('name')!r} must equal the file name "
                         f"{name!r} (state files and locks are keyed by it)")
    return cfg


def storage_dir() -> Path:
    """Data directory. TRIDENT_STORAGE_DIR (process env, then .env) overrides system.yaml."""
    override = env("TRIDENT_STORAGE_DIR")
    d = Path(override) if override else PROJECT_ROOT / load_system_config().get("storage_dir", "storage")
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    """Log directory. TRIDENT_LOG_DIR (process env, then .env) overrides the default logs/."""
    override = env("TRIDENT_LOG_DIR")
    d = Path(override) if override else PROJECT_ROOT / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_only_keys() -> tuple[str | None, str | None]:
    """Read-only key. Public market data needs no auth; this key is only used
    to query private account data."""
    return env("BINANCE_READ_API_KEY"), env("BINANCE_READ_API_SECRET")


def trade_keys() -> tuple[str, str]:
    """Trading key. Design rule: keys live only in .env, and only the live
    executors (execution/live.py, execution/live_perp.py) and the read-only key
    self-check (scripts/verify_trade_key.py) may call this. No other module
    should import it."""
    if os.name == "posix" and ENV_FILE.exists():
        if ENV_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RuntimeError("Refusing trade credentials: .env permissions must be 600")
    key, secret = env("BINANCE_TRADE_API_KEY"), env("BINANCE_TRADE_API_SECRET")
    if not key or not secret:
        raise RuntimeError("BINANCE_TRADE_API_KEY/SECRET not set (see .env.example)")
    return key, secret


def credential_values() -> set[str]:
    """Only credentials already loaded by their owner; logging must not load trade keys.

    Retain earlier values after rotation so buffered exceptions cannot expose old keys.
    """
    return set(_redaction_values)
