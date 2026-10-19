"""Deliberate executor halts and the process exit code that signals them."""
from __future__ import annotations

import sys
from typing import Callable

HALT_EXIT_CODE = 78  # EX_CONFIG: halted on purpose; service managers must not auto-restart


class TraderHalted(SystemExit):
    """Deliberate halt (kill, soft cut, liquidation, failed startup safety check).
    Restarting the process will not help; a human must act. str(exc) is the message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


NETWORK_ERROR_EXIT_CODE = 1  # transient: a service manager may restart the process


def exit_on_halt(fn: Callable[[], object]) -> None:
    """Run an executor entry point; turn TraderHalted into HALT_EXIT_CODE.

    An uncaught ccxt.NetworkError (timeout, exchange unavailable, ...) is transient: it
    prints a redacted one-line message instead of a traceback (whose exception text may
    carry signed query strings) and exits with NETWORK_ERROR_EXIT_CODE so the service
    manager restarts the process."""
    import ccxt  # local import: keeps this module importable without side effects

    try:
        fn()
    except TraderHalted as e:
        print(f"HALTED: {e.message}", file=sys.stderr)
        sys.exit(HALT_EXIT_CODE)
    except ccxt.NetworkError as e:
        from execution.common import redact  # local import: common imports this module
        print(f"NETWORK ERROR ({type(e).__name__}): {redact(e)} - transient, safe to restart",
              file=sys.stderr)
        sys.exit(NETWORK_ERROR_EXIT_CODE)
