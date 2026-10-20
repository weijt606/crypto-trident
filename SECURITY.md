# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's
"Report a vulnerability" (private security advisory) on this repository instead.
We aim to acknowledge reports within a few days.

If the "Report a vulnerability" button is unavailable, open a public issue titled
"Security contact request" with no technical details, and a maintainer will reach out privately.

## Operating Trident safely

Trident can place real orders with real money. Before running anything live:

- **API keys live only in `.env`** (gitignored, `chmod 600`). Never put keys in code,
  configs, issues, logs or commit messages.
- Use **two keys**: a read-only key for data/monitoring, and a trade key. Only the live
  executors and `scripts/verify_trade_key.py` read the trade key from `.env`. `.env` is
  parsed by `core.config` and is not exported into the process environment, so child
  processes and unrelated modules do not inherit it.
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
  restarts; only `scripts/revive.py`, run by a human, clears a kill.
- Live execution requires Linux/macOS POSIX file locking; unsupported platforms halt.
  `.env` must not be accessible to group/others on POSIX; trade-key loading refuses otherwise.
  Use `umask 077` for manual runs. Do not share accounts/symbols between bots.
- Only one process may manage a bot: executors take a lock on `<live_dir>/<bot>.lock` and a
  second instance exits with code 78. Orders are never re-sent for a decided bar;
  a timed-out send is resolved by client-id lookup.
- Exchange error text is redacted (URL query strings and signatures stripped) before it
  reaches logs, the event table or alerts.
- Risk checks run on closed bars, not continuously; no exchange-hosted stop is installed.
  Thresholds do not cap losses during intrabar moves, gaps or outages.
- Lightweight CI runs tests, scans history and working files, and verifies vendored file
  hashes. A manual CI run additionally audits pinned Python and bundled npm dependencies.
  CI does not connect to an operator account, deploy code or run trading services.
  See `docs/RELEASE_READINESS.md`.
