"""monitor.alerts: Telegram config validation and alerts.jsonl rotation."""
import urllib.request

from monitor import alerts


def test_valid_telegram_config():
    assert alerts.valid_telegram_config("123456:ABC-def_ghi", "-1001234")
    assert alerts.valid_telegram_config("123456:ABC", "@my_channel")
    assert not alerts.valid_telegram_config("123456:ABC/../x", "1")
    assert not alerts.valid_telegram_config("abc:def", "1")
    assert not alerts.valid_telegram_config("123:abc?x=1", "1")
    assert not alerts.valid_telegram_config("123:abc", "1&text=x")
    assert not alerts.valid_telegram_config("", "1")


def test_malformed_token_is_not_sent_or_logged(monkeypatch, caplog):
    bad = "123:abc/evil"
    monkeypatch.setattr(alerts, "env",
                        lambda k, d=None: {"TELEGRAM_BOT_TOKEN": bad, "TELEGRAM_CHAT_ID": "1"}.get(k, d))
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(a))
    assert alerts.send_alert("[WARNING] test") is False
    assert not called
    assert bad not in caplog.text


def test_rotation_keeps_one_backup(tmp_path):
    p = tmp_path / "alerts.jsonl"
    p.write_text("x" * 100)
    alerts._rotate_if_needed(p, max_bytes=50)
    assert not p.exists() and (tmp_path / "alerts.jsonl.1").read_text() == "x" * 100
    p.write_text("y" * 100)
    alerts._rotate_if_needed(p, max_bytes=50)
    assert (tmp_path / "alerts.jsonl.1").read_text() == "y" * 100
    p.write_text("small")
    alerts._rotate_if_needed(p, max_bytes=50)
    assert p.read_text() == "small"
    alerts._rotate_if_needed(tmp_path / "missing.jsonl")  # no error
