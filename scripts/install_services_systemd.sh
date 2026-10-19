#!/bin/bash
# Install/update systemd --user units for trident (Linux).
# Run as the account that owns the checkout. To keep services running after logout:
#   loginctl enable-linger "$USER"
#
# Units written:
#   trident-paper-<bot>    one per config/bots/*.yaml with stage: paper (Restart=always)
#   trident-dashboard      web dashboard, listens on 127.0.0.1 only (Restart=always)
#   trident-sentiment      hourly timer -> scripts/fetch_sentiment.py   (optional pipeline)
#   trident-health         hourly timer -> scripts/health.py
#   trident-live-<bot>     only with --live <bot>; the bot config must have stage: live,
#                          set by a human operator. Never run the same live bot in two places.
#
# Executors that halt on purpose (kill switch, soft cut, failed startup safety check)
# exit with code 78; units set RestartPreventExitStatus=78 so they stay down until a
# human has reviewed the cause.
#
# Usage:
#   scripts/install_services_systemd.sh                 # paper bots + dashboard + timers
#   scripts/install_services_systemd.sh --live <bot>    # additionally enable a live bot
#   scripts/install_services_systemd.sh --no-sentiment  # skip the sentiment timer
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
# The path is interpolated into unit files (ExecStart splits on spaces, systemd
# expands % specifiers) and shell commands, so allow only plain path characters.
if [[ ! "$PROJECT_DIR" =~ ^[A-Za-z0-9_./-]+$ ]]; then
    echo "error: project path may only contain A-Z a-z 0-9 _ . / - : $PROJECT_DIR"; exit 1
fi
HALT_EXIT_CODE=78
valid_name() { [[ "$1" =~ ^[A-Za-z0-9_.-]+$ ]]; }

# Validate arguments before touching anything (uv sync, unit files).
LIVE_BOT=""; SENTIMENT=1
while [ $# -gt 0 ]; do
    case "$1" in
        --live)
            LIVE_BOT="${2:?--live needs a bot name}"; shift 2
            valid_name "$LIVE_BOT" || { echo "error: invalid bot name: $LIVE_BOT"; exit 2; } ;;
        --no-sentiment) SENTIMENT=0; shift ;;
        *) echo "unknown argument: $1"; exit 2 ;;
    esac
done

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
"$UV" sync --quiet --project "$PROJECT_DIR"
PY="$PROJECT_DIR/.venv/bin/python"
[ -x "$PY" ] || { echo "error: $PY not found"; exit 1; }
UNIT_DIR="$HOME/.config/systemd/user"
LOG_DIR="$PROJECT_DIR/logs/services"
mkdir -p "$UNIT_DIR" "$LOG_DIR"

svc() {   # svc <name> <always|oneshot> <cmd...>
    local name="$1" mode="$2"; shift 2
    cat > "$UNIT_DIR/trident-$name.service" <<UNIT
[Unit]
Description=trident $name
After=network-online.target
Wants=network-online.target

[Service]
Type=$([ "$mode" = always ] && echo simple || echo oneshot)
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=yes
UMask=0077
ExecStart=$*
$([ "$mode" = always ] && printf 'Restart=always\nRestartSec=30\nRestartPreventExitStatus=%s' "$HALT_EXIT_CODE" || true)
StandardOutput=append:$LOG_DIR/$name.log
StandardError=append:$LOG_DIR/$name.log

[Install]
WantedBy=default.target
UNIT
}

hourly_timer() {  # hourly_timer <name>
    cat > "$UNIT_DIR/trident-$1.timer" <<UNIT
[Unit]
Description=trident $1 timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
}

# Read "<name> <stage> <market>" for every bot config.
bot_list() {
    "$PY" - "$PROJECT_DIR/config/bots" <<'PYEOF'
import sys, pathlib, yaml
for p in sorted(pathlib.Path(sys.argv[1]).glob("*.yaml")):
    c = yaml.safe_load(p.read_text()) or {}
    print(p.stem, c.get("stage", ""), c.get("market", "spot"))
PYEOF
}

PAPER=()
LIVE_MARKET=""
while read -r name stage market; do
    if ! valid_name "$name"; then
        echo "warning: skipping config with invalid name: $name (allowed: A-Z a-z 0-9 _ . -)"
        continue
    fi
    [ "$stage" = paper ] && PAPER+=("$name")
    if [ -n "$LIVE_BOT" ] && [ "$name" = "$LIVE_BOT" ]; then
        [ "$stage" = live ] || { echo "error: $name has stage=$stage, not live (set by a human operator)"; exit 1; }
        LIVE_MARKET="$market"
    fi
done < <(bot_list)
if [ -n "$LIVE_BOT" ] && [ -z "$LIVE_MARKET" ]; then
    echo "error: config/bots/$LIVE_BOT.yaml not found"; exit 1
fi

for name in "${PAPER[@]}"; do
    svc "paper-$name" always "$PY" scripts/run_paper.py --bot "$name"
done
svc dashboard always "$PY" -m monitor.dashboard
svc health oneshot "$PY" scripts/health.py; hourly_timer health
if [ "$SENTIMENT" = 1 ]; then
    svc sentiment oneshot "$PY" scripts/fetch_sentiment.py; hourly_timer sentiment
fi
if [ -n "$LIVE_BOT" ]; then
    runner=scripts/run_live.py
    [ "$LIVE_MARKET" = perp ] && runner=scripts/run_live_perp.py
    svc "live-$LIVE_BOT" always "$PY" "$runner" --bot "$LIVE_BOT"
fi

systemctl --user daemon-reload

for name in "${PAPER[@]}" ; do
    systemctl --user enable --quiet "trident-paper-$name.service"
    systemctl --user restart "trident-paper-$name.service"
    echo "started trident-paper-$name"
done
systemctl --user enable --quiet "trident-dashboard.service"
systemctl --user restart "trident-dashboard.service"
echo "started trident-dashboard"

timers=(health)
[ "$SENTIMENT" = 1 ] && timers+=(sentiment)
for t in "${timers[@]}"; do
    systemctl --user enable --quiet --now "trident-$t.timer"
    echo "enabled trident-$t.timer"
done

if [ -n "$LIVE_BOT" ]; then
    systemctl --user enable --quiet "trident-live-$LIVE_BOT.service"
    systemctl --user restart "trident-live-$LIVE_BOT.service"
    echo "started trident-live-$LIVE_BOT (REAL MONEY)"
fi

echo "status: systemctl --user list-units 'trident*'   logs: $LOG_DIR/"
