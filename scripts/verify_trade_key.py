"""Self-check that the trade API key's permissions follow the safety rules.

Read-only calls only (key restrictions + spot balances): no orders, no transfers, and the
key is never printed. Besides the live executors, this is the only place that loads the
BINANCE_TRADE_* keys from .env.

Usage: uv run python scripts/verify_trade_key.py [--max-spot-usdt N]
Exit code: 0 = all checks pass; 1 = something must be fixed in the exchange's API settings.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ccxt

from core.config import trade_keys
from execution.common import permission_value, redact


def main() -> int:
    p = argparse.ArgumentParser(description="Verify trade API key permissions (read-only).")
    p.add_argument("--max-spot-usdt", type=float, default=None,
                   help="optional: fail if the spot wallet holds more than this (USDT value), "
                        "to enforce keeping only trading capital on the spot account")
    args = p.parse_args()

    try:
        key, secret = trade_keys()
    except RuntimeError as e:
        print(f"[FAIL] {redact(e)}")
        return 1

    ex = ccxt.binance({"apiKey": key, "secret": secret, "enableRateLimit": True})

    try:
        r = ex.sapi_get_account_apirestrictions()
    except ccxt.AuthenticationError as e:
        print("[FAIL] authentication failed (wrong key/secret, or this IP is not whitelisted): "
              f"{redact(e)}")
        return 1

    def b(field: str) -> bool:
        return permission_value(r, field)

    # (field, required value, label). Futures permission is handled separately below:
    # it is required for perp bots and does not break fund isolation by itself.
    checks = [
        ("enableReading", True, "reading"),
        ("enableSpotAndMarginTrading", True, "spot & margin trading"),
        ("ipRestrict", True, "IP whitelist bound"),
        ("enableWithdrawals", False, "withdrawals (must never be enabled)"),
        ("permitsUniversalTransfer", False, "universal transfer (fund isolation)"),
        ("enableInternalTransfer", False, "internal transfer"),
        ("enableMargin", False, "margin borrowing"),
        ("enableVanillaOptions", False, "options"),
    ]
    ok = True
    for field, want, label in checks:
        got = b(field)
        mark = "[OK]  " if got == want else "[FAIL]"
        ok = ok and got == want
        state = "unknown" if got is None else ("on" if got else "off")
        print(f"{mark} {label}: {state}"
              + ("" if got == want else f" (must be {'on' if want else 'off'}; change it in API management)"))

    # Futures permission: needed only for perp bots (run_live_perp.py). Not a hard failure.
    if b("enableFutures"):
        print("[OK]   futures: on (needed for perp bots; withdrawals/transfers verified off)")
    else:
        print("[INFO] futures: off (spot live trading only; enable futures to run perp bots)")

    total_usdt = 0.0
    print("\nNon-zero spot balances:")
    balance = ex.fetch_balance()
    tickers = None
    for asset, amt in sorted(balance["total"].items()):
        if not amt:
            continue
        if asset in ("USDT", "USDC", "FDUSD"):
            value = amt
        else:
            if tickers is None:
                tickers = ex.fetch_tickers()
            t = tickers.get(f"{asset}/USDT")
            value = amt * t["last"] if t and t["last"] else 0.0
        total_usdt += value
        print(f"  {asset}: {amt:.8g} (~{value:,.2f} USDT)")
    print(f"spot total ~ {total_usdt:,.2f} USDT", end=" ")
    if args.max_spot_usdt is None:
        print()
    elif total_usdt <= args.max_spot_usdt:
        print(f"[OK] within --max-spot-usdt {args.max_spot_usdt:,.0f}")
    else:
        ok = False
        print(f"[FAIL] above --max-spot-usdt {args.max_spot_usdt:,.0f}; move the excess to another wallet")

    print("\n" + ("All checks passed." if ok else "Some checks failed; fix them and re-run."))
    return 0 if ok else 1


def _main_redacted() -> int:
    """Any other exchange error (network, rejected request) prints a redacted one-liner
    instead of a traceback: exception text can carry signed query strings."""
    try:
        return main()
    except ccxt.BaseError as e:
        print(f"[FAIL] exchange error ({type(e).__name__}): {redact(e)}")
        return 1
    except Exception as e:
        print(f"[FAIL] self-check error ({type(e).__name__}): {redact(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(_main_redacted())
