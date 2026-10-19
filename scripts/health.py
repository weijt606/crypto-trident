"""System health check: data freshness, process heartbeats, live executor state, error logs.

Intended to run on a timer. Exits 1 and sends an alert if anything is wrong.

Usage:
    uv run python scripts/health.py
"""
import _bootstrap  # noqa: F401
import argparse
import re
import sys
import time
from pathlib import Path

import yaml

from core.config import CONFIG_DIR, log_dir, storage_dir
from core.models import TIMEFRAME_SECONDS
from data.store import TradeLog
from monitor.alerts import send_alert
from sentiment.store import load_composite

HOUR_MS = 3_600_000
POLL_FAIL_STREAK = 10  # consecutive polling failures at/above this = not self-healing (network/rate limit)
OTHER_ERR_LIMIT = 5  # more non-polling ERROR lines than this = a real problem
LIVE_EVENT_WINDOW_H = 72  # look-back window for live executor failure events
# Events meaning the executor is alive but cannot trade, or a flatten left a residual.
LIVE_ALERT_KINDS = ("auth_fail", "order_error", "reject", "flatten_incomplete")
LIVE_BEAT_STALE_H = 2      # live heartbeat older than this = process stuck or exited

# Matches executor log lines like "Polling error (3 in a row): ..."
_POLL_ERR = re.compile(r"polling error \((\d+) in a row\)", re.I)


def classify_log_errors(lines: list[str]) -> tuple[list[str], list[str]]:
    """Classify recent ERROR log lines, returning (ok messages, problem messages).

    Polling errors are caught and retried by the executors; almost all of them are
    one-off network blips and must not mark the system unhealthy:
    - polling errors: only a problem if the highest streak >= POLL_FAIL_STREAK
    - other ERRORs: a problem if there are more than OTHER_ERR_LIMIT

    Only the ERROR level field (" ERROR [module]") is matched, so WARNING lines that
    merely mention "ERROR" (such as this check's own alert text) are not counted.
    """
    ok: list[str] = []
    problems: list[str] = []
    poll_streaks: list[int] = []
    n_other = 0
    for line in lines:
        if not re.search(r" ERROR \[", line):
            continue
        m = _POLL_ERR.search(line)
        if m:
            poll_streaks.append(int(m.group(1)))
        else:
            n_other += 1
    if poll_streaks:
        max_streak = max(poll_streaks)
        if max_streak >= POLL_FAIL_STREAK:
            problems.append(
                f"persistent polling failures (max streak {max_streak}, {len(poll_streaks)} lines)"
                " - check network / rate limits"
            )
        else:
            ok.append(
                f"transient polling errors: {len(poll_streaks)} (all self-healed, max streak {max_streak})"
            )
    (ok if n_other <= OTHER_ERR_LIMIT else problems).append(f"other recent ERROR lines: {n_other}")
    return ok, problems


def check_live_executors(names: list[str], live_dir: Path) -> tuple[list[str], list[str]]:
    """Live executor health: a kill file means the executor has halted.

    A tripped live executor writes <live_dir>/<name>.kill and refuses to restart. A
    service manager may keep restarting it (exiting immediately) without producing
    any ERROR-level log, and paper heartbeat checks do not cover live bots, so a kill
    file is reported as a top-priority problem until a human reviews and revives it.
    A <name>.softcut marker is also a deliberate halt, not a stale heartbeat: a
    controlled retirement ("retired: ..." reason) is reported as OK, any other soft
    cut as a problem needing review. Without either marker, heartbeat freshness is checked so that a crashed or never
    started process is not reported as online.
    """
    from monitor.heartbeat import last_beat

    ok: list[str] = []
    problems: list[str] = []
    now_ms = int(time.time() * 1000)
    for name in names:
        kill_file = live_dir / f"{name}.kill"
        if kill_file.exists():
            info = kill_file.read_text().strip()
            problems.append(
                f"{name}: live executor halted by circuit breaker ({info}); "
                "a human operator must review the cause and run scripts/revive.py"
            )
            continue
        softcut_file = live_dir / f"{name}.softcut"
        if softcut_file.exists():
            info = softcut_file.read_text().strip()
            if "reason=retired:" in info:
                ok.append(f"{name}: live executor retired (halted by .softcut marker: {info})")
            else:
                problems.append(
                    f"{name}: live executor halted by soft cut ({info}); review and re-tune, "
                    f"then delete {softcut_file.name} to resume"
                )
            continue
        hb = last_beat(name)
        if hb is None:
            problems.append(f"{name}: live executor has no heartbeat file (process not started?)")
        elif now_ms - hb > LIVE_BEAT_STALE_H * HOUR_MS:
            problems.append(
                f"{name}: live executor heartbeat stale for {(now_ms - hb) / HOUR_MS:.1f}h "
                "(process stuck or exited)"
            )
        else:
            ok.append(f"{name}: live executor online (no kill file, heartbeat {(now_ms - hb) / 60000:.0f} min ago)")
    return ok, problems


def check_live_events(live_dir: Path, now_ms: int) -> tuple[list[str], list[str]]:
    """Runtime failure events of live executors (auth failures, order errors).

    An executor can be running, with no kill file and a fresh heartbeat, yet be unable
    to trade, e.g. because its API key rejects the current egress IP. This scans the
    live event log for failure events within LIVE_EVENT_WINDOW_H hours.

    Limitation: this is a scan of past events, not a live authentication probe. No
    events can simply mean no API calls were made. Use scripts/verify_trade_key.py
    to actually test the key.
    """
    db_path = live_dir / "live.sqlite"
    if not db_path.exists():
        return [], []
    df = TradeLog(db_path).events_df()
    if df.empty:
        return [f"no live failure events in the last {LIVE_EVENT_WINDOW_H}h"], []
    cutoff = now_ms - LIVE_EVENT_WINDOW_H * HOUR_MS
    problems: list[str] = []
    hits = df[(df["kind"].isin(LIVE_ALERT_KINDS)) & (df["ts"] >= cutoff)]
    if hits.empty:
        return [f"no live failure events in the last {LIVE_EVENT_WINDOW_H}h"], problems
    for _, row in hits.sort_values("ts").iterrows():
        age_h = (now_ms - int(row["ts"])) / HOUR_MS
        problems.append(
            f"{row['bot']}: live executor failure event {row['kind']} ({age_h:.1f}h ago)"
            f" - {str(row['detail'])[:90]}; executor may be unable to trade, needs attention"
        )
    return [], problems


def main() -> int:
    argparse.ArgumentParser(
        description="Check data freshness, executor heartbeats, live failure events and "
                    "recent ERROR log lines. Exits 1 and sends an alert on any problem."
    ).parse_args()
    now_ms = int(time.time() * 1000)
    problems: list[str] = []
    ok: list[str] = []

    # 1) Sentiment composite freshness (daily data + fetch interval; >30h = fetcher stalled).
    #    Skipped if the optional sentiment pipeline has never been run.
    composite = load_composite()
    if composite is not None and not composite.empty:
        age_h = (now_ms - int(composite["avail_ts"].iloc[-1])) / HOUR_MS
        (ok if age_h <= 30 else problems).append(
            f"latest sentiment value is {age_h:.1f}h old" + ("" if age_h <= 30 else " (fetcher stalled?)")
        )

    # 2) Paper bots: equity records act as a heartbeat. Live bots are checked below.
    tlog = TradeLog()
    live_names: list[str] = []
    for path in sorted((CONFIG_DIR / "bots").glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text())
        stage = cfg.get("stage")
        if stage == "live":
            live_names.append(cfg["name"])
            continue
        if stage != "paper":
            continue
        state = tlog.latest_state(cfg["name"])
        limit_ms = 2 * TIMEFRAME_SECONDS[cfg["timeframe"]] * 1000 + HOUR_MS
        if state is None:
            problems.append(f"{cfg['name']}: no paper equity records (process not started?)")
        elif now_ms - state["ts"] > limit_ms:
            problems.append(
                f"{cfg['name']}: heartbeat stale for {(now_ms - state['ts']) / HOUR_MS:.1f}h (process down?)"
            )
        else:
            ok.append(f"{cfg['name']}: heartbeat ok")

    # 3) Live executors: kill files and heartbeats.
    live_dir = storage_dir() / "live"
    live_ok, live_problems = check_live_executors(live_names, live_dir)
    ok.extend(live_ok)
    problems.extend(live_problems)

    # 4) Live runtime failure events (executor online but unable to trade).
    evt_ok, evt_problems = check_live_events(live_dir, now_ms)
    ok.extend(evt_ok)
    problems.extend(evt_problems)

    # 5) Recent ERROR lines in the main log (see classify_log_errors).
    log_file = log_dir() / "trident.log"
    if log_file.exists():
        recent = log_file.read_text().splitlines()[-300:]
        log_ok, log_problems = classify_log_errors(recent)
        ok.extend(log_ok)
        problems.extend(log_problems)

    for line in ok:
        print(f"[OK]   {line}")
    for line in problems:
        print(f"[FAIL] {line}")

    if problems:
        send_alert("[CRITICAL] trident health check failed:\n" + "\n".join(problems))
        return 1
    print("All health checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
