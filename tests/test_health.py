"""Health check tests: transient polling blips must not alert, real failures must."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from conftest import T0
from health import (
    LIVE_EVENT_WINDOW_H,
    OTHER_ERR_LIMIT,
    POLL_FAIL_STREAK,
    check_live_events,
    check_live_executors,
    classify_log_errors,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import TradeLog  # noqa: E402

HOUR_MS = 3_600_000


def _poll_line(streak: int) -> str:
    return (
        f"2024-01-15 07:15:08,412 ERROR [paper] Polling error ({streak} in a row): "
        "binance GET https://api.binance.com/api/v3/klines?interval=1d&limit=3&symbol=BTCUSDT"
    )


def test_transient_polling_jitter_is_ok():
    # Only single, self-healed blips -> not a failure
    ok, problems = classify_log_errors([_poll_line(1) for _ in range(50)])
    assert problems == []
    assert any("transient polling errors" in m and "self-healed" in m for m in ok)


def test_sustained_polling_failure_alerts():
    # A streak at the threshold = real outage / rate limit -> must alert
    lines = [_poll_line(1), _poll_line(POLL_FAIL_STREAK)]
    ok, problems = classify_log_errors(lines)
    assert any("persistent polling failures" in m for m in problems)


def test_warning_line_mentioning_error_not_counted():
    # The check's own alert text is a WARNING line containing "ERROR"; must not be counted
    warn = "2024-01-15 07:31:44,905 WARNING [alerts] recent log has ERROR 7 lines"
    ok, problems = classify_log_errors([warn])
    assert problems == []
    assert any("other recent ERROR lines: 0" in m for m in ok)


def test_real_other_errors_alert_over_limit():
    err = "2024-01-15 09:00:00,000 ERROR [paper] order failed: insufficient balance"
    over = [err] * (OTHER_ERR_LIMIT + 1)
    ok, problems = classify_log_errors(over)
    assert any("other recent ERROR" in m for m in problems)
    # exactly at the limit -> no alert
    ok2, problems2 = classify_log_errors([err] * OTHER_ERR_LIMIT)
    assert problems2 == []


def test_live_kill_file_is_critical_problem(tmp_path):
    # A kill file means a live executor is down -> must be reported
    (tmp_path / "demo-bot.kill").write_text("ts=1700000000000 reason=drawdown 16.0% >= 15%\n")
    ok, problems = check_live_executors(["demo-bot"], tmp_path)
    assert ok == []
    assert any("demo-bot" in m and "circuit breaker" in m and "revive" in m for m in problems)


def test_live_no_kill_file_is_ok(tmp_path, monkeypatch):
    # No kill file + fresh heartbeat = online (heartbeat mocked so the test is environment-independent)
    import time
    monkeypatch.setattr("monitor.heartbeat.last_beat", lambda name: int(time.time() * 1000))
    ok, problems = check_live_executors(["demo-bot"], tmp_path)
    assert problems == []
    assert any("demo-bot" in m and "online" in m for m in ok)


def test_live_missing_heartbeat_is_problem(tmp_path, monkeypatch):
    monkeypatch.setattr("monitor.heartbeat.last_beat", lambda name: None)
    ok, problems = check_live_executors(["demo-bot"], tmp_path)
    assert ok == []
    assert any("no heartbeat" in m for m in problems)


def test_live_check_empty_when_no_live_bot(tmp_path):
    ok, problems = check_live_executors([], tmp_path)
    assert ok == [] and problems == []


def test_live_no_db_is_silent(tmp_path):
    # No live database yet -> neither errors nor noise
    ok, problems = check_live_events(tmp_path, now_ms=T0 + 400 * 86_400_000)
    assert ok == [] and problems == []


def test_recent_auth_fail_is_critical_problem(tmp_path):
    # Executor online but authentication failing = cannot trade -> must be reported
    now = T0 + 400 * 86_400_000
    tlog = TradeLog(tmp_path / "live.sqlite")
    tlog.record_event(now - 2 * HOUR_MS, "demo-bot", "auth_fail",
                      "authentication failed: add the egress IP to the key whitelist")
    ok, problems = check_live_events(tmp_path, now_ms=now)
    assert ok == []
    assert any("demo-bot" in m and "auth_fail" in m and "unable to trade" in m for m in problems)


def test_stale_auth_fail_ages_out(tmp_path):
    # Events older than the look-back window no longer alert
    now = T0 + 400 * 86_400_000
    tlog = TradeLog(tmp_path / "live.sqlite")
    tlog.record_event(now - (LIVE_EVENT_WINDOW_H + 5) * HOUR_MS, "demo-bot", "auth_fail", "old event")
    ok, problems = check_live_events(tmp_path, now_ms=now)
    assert problems == []
    assert any("no live failure events" in m for m in ok)


def test_help_does_not_run_checks():
    import subprocess
    script = Path(__file__).resolve().parent.parent / "scripts" / "health.py"
    r = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0 and "usage" in r.stdout.lower()
    assert "[OK]" not in r.stdout and "[FAIL]" not in r.stdout


def test_live_softcut_marker_not_reported_as_stale(tmp_path, monkeypatch):
    # Halted processes stop beating; a .softcut marker must explain that.
    monkeypatch.setattr("monitor.heartbeat.last_beat", lambda name: None)
    (tmp_path / "ret-bot.softcut").write_text("ts=1 reason=retired: strategy sunset\n")
    (tmp_path / "cut-bot.softcut").write_text("ts=1 reason=drawdown 12.0% >= soft cut 10%\n")
    ok, problems = check_live_executors(["ret-bot", "cut-bot"], tmp_path)
    assert not any("heartbeat" in m for m in ok + problems)
    assert any("ret-bot" in m and "retired" in m for m in ok)
    assert any("cut-bot" in m and "soft cut" in m and "cut-bot.softcut" in m for m in problems)
