"""Dashboard hardening: strict JSON, bounded kline cache keys, alert tail,
read-only ledgers, static allow-list."""
import json
import math

import numpy as np
import pandas as pd

from monitor import dashboard


def test_json_has_no_nan_or_infinity():
    payload = {"a": float("nan"), "b": [float("inf"), -float("inf"), 1.5],
               "c": np.float64("nan"), "d": np.int64(7), "e": pd.NaT, "f": (1, float("nan"))}
    out = json.loads(dashboard.to_json(payload))
    assert out == {"a": None, "b": [None, None, 1.5], "c": None, "d": 7, "e": None, "f": [1, None]}
    raw = dashboard.to_json({"x": math.nan}).decode()
    assert "NaN" not in raw and "Infinity" not in raw


def test_kline_limit_buckets():
    assert dashboard.kline_limit("1") == 100
    assert dashboard.kline_limit("101") == 250
    assert dashboard.kline_limit("500") == 500
    assert dashboard.kline_limit("999999") == 2000
    assert dashboard.kline_limit("abc") == 500
    assert {dashboard.kline_limit(str(n)) for n in range(-5, 5000, 7)} <= set(dashboard.KLINE_LIMITS)


def test_alerts_tail_reads_only_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "storage_dir", lambda: tmp_path)
    monkeypatch.setattr(dashboard, "ALERTS_TAIL_BYTES", 4096)
    with open(tmp_path / "alerts.jsonl", "w", encoding="utf-8") as f:
        for i in range(5000):
            f.write(json.dumps({"ts": i, "level": "info", "text": f"msg {i}"}) + "\n")
    alerts = dashboard.api_alerts(None)["alerts"]
    assert alerts and alerts[0]["text"] == "msg 4999"
    assert all(a["ts"] > 4000 for a in alerts)


def test_alerts_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "storage_dir", lambda: tmp_path)
    assert dashboard.api_alerts(None) == {"alerts": []}


def test_dashboard_never_creates_ledgers(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "storage_dir", lambda: tmp_path)
    paper, live = dashboard._open_logs()
    assert live is None and paper.read_only and paper.equity_df("demo-bot").empty
    dashboard._close(paper, live)
    assert not (tmp_path / "trades.sqlite").exists()


def test_static_allow_list_and_csp():
    assert "/static/app.js" in dashboard.STATIC_FILES
    assert "/static/app.css" in dashboard.STATIC_FILES
    for _, (name, _ctype) in dashboard.STATIC_FILES.items():
        assert (dashboard.STATIC_DIR / name).is_file()
    csp = dashboard.SECURITY_HEADERS["Content-Security-Policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src


def test_page_has_no_inline_script_or_handlers():
    html = (dashboard.STATIC_DIR / "dashboard.html").read_text()
    low = html.lower()
    assert "<script>" not in low
    assert "<script src=" in low
    import re
    assert not re.search(r"\son[a-z]+\s*=", low)


def test_handler_has_socket_timeout():
    assert dashboard.Handler.timeout and dashboard.Handler.timeout <= 60


def test_json_handles_numpy_arrays():
    payload = {"arr": np.array([1.0, np.nan, 3.0]), "m": np.array([[1, 2], [3, 4]]),
               "empty": np.array([]), "scalar_arr": np.array(5)}
    out = json.loads(dashboard.to_json(payload))
    assert out["arr"] == [1.0, None, 3.0]
    assert out["m"] == [[1, 2], [3, 4]] and out["empty"] == [] and out["scalar_arr"] == 5
