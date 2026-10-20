"""Regression checks for open-source release security boundaries; no exchange calls."""
import io
import logging
import os
from pathlib import Path
import subprocess

import ccxt
import pytest

from core import config
from execution import common
from execution.halt import HALT_EXIT_CODE, TraderHalted, exit_on_halt
from execution.live import require_live_exchange
from monitor.logger import RedactingFormatter


def safe_restrictions():
    return dict(enableWithdrawals=False, permitsUniversalTransfer=False,
                enableInternalTransfer=False, enableMargin=False, enableVanillaOptions=False,
                enableReading=True, ipRestrict=True, enableSpotAndMarginTrading=True,
                enableFutures=True)


@pytest.mark.parametrize('need,field', [
    (need, field) for need in ('spot', 'futures') for field in safe_restrictions()
    if field != ('enableFutures' if need == 'spot' else 'enableSpotAndMarginTrading')
])
def test_permission_unknown_or_malformed_refuses(field, need):
    restrictions = safe_restrictions()
    del restrictions[field]
    assert any(field in p for p in common.key_restriction_problems(restrictions, need))
    for malformed in (None, 0, 1, [], 'unknown'):
        restrictions[field] = malformed
        assert any(field in p for p in common.key_restriction_problems(restrictions, need))


def test_permissions_accept_boolean_and_exchange_string_values():
    for need in ('spot', 'futures'):
        assert common.key_restriction_problems(safe_restrictions(), need) == []
        strings = {k: str(v).lower() for k, v in safe_restrictions().items()}
        assert common.key_restriction_problems(strings, need) == []
    assert common.key_restriction_problems([], 'spot')


def test_unsupported_lock_platform_never_creates_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(common, 'fcntl', None)
    with pytest.raises(TraderHalted, match='POSIX'):
        common.InstanceLock(tmp_path, 'demo')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('exc_type', [ccxt.ExchangeError, ccxt.AuthenticationError, ValueError])
def test_entrypoint_error_does_not_escape_to_raw_traceback(exc_type, capsys):
    value = 'Audit_' * 8
    def fail():
        raise exc_type('https://example.invalid/account?signature=' + value)
    with pytest.raises(SystemExit) as exc:
        exit_on_halt(fail)
    assert exc.value.code == HALT_EXIT_CODE
    out = capsys.readouterr().err
    assert value not in out and 'Traceback' not in out and '<redacted>' in out


@pytest.mark.parametrize('template', [
    'X-MBX-APIKEY: {}', '{{"api_key": "{}"}}', "secret='{}'", 'password={}',
    'BINANCE_TRADE_API_SECRET={}', 'Authorization: Bearer {}',
    'https://api.telegram.org/bot{}/sendMessage',
])
def test_redaction_supports_credentials_with_punctuation(template):
    value = 'Audit_./+-' * 5
    assert value not in common.redact(template.format(value))


def test_configured_secret_without_label_is_redacted(tmp_path, monkeypatch):
    value = 'Audit_' * 8
    path = tmp_path / '.env'
    path.write_text('BINANCE_TRADE_API_SECRET=' + value)
    path.chmod(0o600)
    monkeypatch.setattr(config, 'ENV_FILE', path)
    monkeypatch.delenv('BINANCE_TRADE_API_SECRET', raising=False)
    assert config.env('BINANCE_TRADE_API_SECRET') == value
    assert value not in common.redact('unexpected response: ' + value)


def test_logging_does_not_load_trade_keys(monkeypatch):
    def forbidden():
        pytest.fail('Redaction must not parse .env or load unrelated credentials')
    monkeypatch.setattr(config, '_dotenv', forbidden)
    assert common.redact('ordinary log message') == 'ordinary log message'


def test_logger_redacts_exception_traceback():
    value = 'Audit_' * 8
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(RedactingFormatter('%(message)s'))
    logger = logging.Logger('release-security')
    logger.addHandler(handler)
    try:
        raise ValueError('secret=' + value)
    except ValueError:
        logger.exception('request failed')
    assert value not in output.getvalue() and 'ValueError' in output.getvalue()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permission contract')
def test_trade_key_refuses_readable_dotenv(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('')
    path.chmod(0o644)
    monkeypatch.setattr(config, 'ENV_FILE', path)
    with pytest.raises(RuntimeError, match='permissions'):
        config.trade_keys()


def test_hook_does_not_echo_detected_credential(tmp_path):
    hook = Path(__file__).resolve().parents[1] / 'scripts/githooks/pre-commit'
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    value = 'Audit' * 8
    (tmp_path / 'example.txt').write_text('api_key=' + value + '\n')
    subprocess.run(['git', 'add', 'example.txt'], cwd=tmp_path, check=True)
    result = subprocess.run(['sh', str(hook)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert value not in result.stdout + result.stderr
    assert 'withheld' in result.stdout


def test_unsupported_live_exchange_refused():
    require_live_exchange('binance')
    with pytest.raises(TraderHalted, match='Unsupported live exchange'):
        require_live_exchange('unimplemented')


def test_bot_name_rejects_trailing_newline():
    with pytest.raises(ValueError):
        config.validate_bot_name('demo\n')


def test_reddit_requires_approval_before_network(monkeypatch):
    from sentiment import sources
    from sentiment.composite import SentimentConfig
    monkeypatch.setattr(sources, 'env', lambda *a, **k: None)
    def network_forbidden(*a, **k):
        pytest.fail('Reddit must not make a network request before access approval')
    monkeypatch.setattr(sources, '_reddit_get', network_forbidden)
    assert sources.fetch_reddit(cfg=SentimentConfig(sources=('reddit',))).empty


def test_alert_redacts_before_persistence_and_push(monkeypatch):
    from monitor import alerts
    value = 'Audit_' * 8
    saved = []
    monkeypatch.setattr(alerts, '_persist', lambda text, level: saved.append(text))
    monkeypatch.setattr(alerts, 'env', lambda *a, **k: None)
    assert alerts.send_alert('secret=' + value) is False
    assert saved and value not in saved[0]
