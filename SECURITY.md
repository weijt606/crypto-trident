# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's
"Report a vulnerability" (private security advisory) on this repository instead.
Reports are handled on a best-effort basis by a personal-project maintainer; response times
are not guaranteed.

If the "Report a vulnerability" button is unavailable, open a public issue titled
"Security contact request" with no technical details to ask the maintainer to arrange a
private reporting channel. Never include credentials or exploit details in that issue.

## Operating Trident safely

Trident can place real orders with real money. Before running anything live:

- **Supply API keys through `.env`** (gitignored, `chmod 600`) **or an externally managed
  process environment.** Environment values take precedence. Never put keys in code,
  shared configs, issues, logs or commit messages.
- Public data, paper trading and the dashboard need no key. The `BINANCE_READ_*` settings
  and `read_only_keys()` helper are reserved for private read-only integrations and are
  not used by the bundled scripts. Keep any such key separate from trading credentials.
  Only live executors and `scripts/verify_trade_key.py` call `trade_keys()`. `core.config`
  does not export `.env` values into `os.environ`; externally injected environment values
  retain normal child-process inheritance.
- Trade key permissions: reading + spot/futures trading only. **Disable withdrawals,
  universal and internal transfer, margin borrowing and options**, bind an **IP whitelist**,
  and prefer a dedicated sub-account that holds only the capital you are willing to lose.
  The live executors re-check this at every start and refuse to run with a dangerous key;
  unknown permission fields or a missing IP whitelist refuse startup.
- Run `scripts/verify_trade_key.py` to check the key's permissions before going live.
- The repo ships a pre-commit secret scanner: `git config core.hooksPath scripts/githooks`.
- The dashboard binds to `127.0.0.1` only, rejects requests with a foreign `Host` header
  (DNS-rebinding defense) and loads no third-party resources. If you run it on a server,
  reach it through an SSH tunnel; do not expose it to the internet.
- Outbound calls: exchange APIs, the enabled sentiment sources, `api.ipify.org` (queried only
  after an exchange authentication failure to report your outbound IP) and, only if
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are set, `api.telegram.org` (alert text, IP masked).
  Alert text can disclose symbols, quantities, prices, capital and bot names to Telegram;
  leave Telegram settings empty for local-only alerts.
- Strategy orders go through `safety.guard.RiskGuard`. Emergency exposure-reducing flatten
  paths are the documented exceptions; do not add other bypasses.
- Live safety state (drawdown peak, daily halt, kill) is persisted and restored across
  restarts; `scripts/revive.py` is the documented human-reviewed way to clear a live kill.
  Files can be changed by the operating-system user; this is not a tamper-proof boundary.
  Paper account recovery does not restore the live executor's persisted breaker state.
- Live execution requires Linux/macOS POSIX file locking; unsupported platforms halt.
  `.env` must not be accessible to group/others on POSIX; trade-key loading refuses otherwise.
  Use `umask 077` for manual runs. Ensure custom storage/log directories are gitignored too.
  Do not share accounts/symbols between bots.
- Only one process may manage a bot: executors take a lock on `<live_dir>/<bot>.lock` and a
  second instance exits with code 78. Orders are never re-sent for a decided bar;
  a timed-out send triggers client-id lookup. Failed lookup can still leave the outcome
  unknown, and marking a bar before sending can skip a signal after a crash. Review the
  account before recovery; these mechanisms do not guarantee exactly-once execution.
- Exchange error text is redacted (URL query strings and signatures stripped) before it
  reaches the implemented exchange-error log/event/alert paths. Log formatting and alert
  persistence/push also redact known patterns and already-loaded credential values. This
  does not identify every possible secret or remove account and trade details; review
  runtime files manually before sharing.
- Risk checks run on closed bars, not continuously; no exchange-hosted stop is installed.
  Thresholds do not cap losses during intrabar moves, gaps or outages.
- Lightweight CI runs tests, scans history and working files, and verifies vendored file
  hashes. A manual CI run additionally audits pinned Python and bundled npm dependencies.
  CI does not connect to an operator account, deploy code or run trading services.
  See `docs/RELEASE_READINESS.md`.
