# Contributing

Thanks for your interest in Trident!

1. Fork, create a branch, and keep changes focused.
2. `uv sync && uv run pytest` must pass. Add tests for new behavior.
3. Enable the secret-scanning hook: `git config core.hooksPath scripts/githooks`.
4. Respect the design rules in `docs/ARCHITECTURE.md`, especially:
   - strategy code must not branch on backtest/paper/live;
   - every order must pass `RiskGuard.check_order` (the only exceptions are the documented
     reduce-only flatten paths);
   - no secrets or personal account data in code, configs or commits.
5. Do not submit PRs that claim or advertise strategy profitability. Example strategies are
   for demonstrating the API only.
6. Do not add third-party resources (CDNs, web fonts, analytics) to the dashboard; vendor
   assets under `monitor/static/` with their license.
   Keep page code in `monitor/static/app.js` (the CSP forbids inline scripts and inline
   event handlers) and register every served file in `STATIC_FILES` in `monitor/dashboard.py`.

## License of contributions

Trident is licensed under the Apache License, Version 2.0. Unless you explicitly state
otherwise, any contribution you intentionally submit for inclusion is licensed under the
same terms, as described in Section 5 of the [LICENSE](LICENSE), without additional terms
or conditions.
