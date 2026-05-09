#!/usr/bin/env bash
# Idempotent VPS bring-up script. Run as root on a fresh Ubuntu 22.04 host.
# Steps it performs:
#   - install OS deps
#   - create the `scraper` system user
#   - clone the repo into /opt/scraper if missing
#   - install uv if missing
#   - run `uv sync` and install the Chromium browser via Playwright
#   - copy systemd units into place
#   - reload systemd
#
# What it does NOT do:
#   - log into SportyBet (run scripts/bootstrap_login.py over a VNC tunnel)
#   - write a config.yaml or .env (copy from the *.example files manually)
#   - enable the units (do that after smoke-testing capture by hand)

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/truthixify/sporty.git}"
TARGET="${TARGET:-/opt/scraper}"
USER_NAME="${USER_NAME:-scraper}"

echo "=> apt deps"
apt-get update -y
apt-get install -y python3 python3-venv git tmux fail2ban ufw curl ca-certificates

echo "=> system user"
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$USER_NAME"
fi

echo "=> clone or update repo"
if [ ! -d "$TARGET/.git" ]; then
  git clone "$REPO_URL" "$TARGET"
fi
chown -R "$USER_NAME:$USER_NAME" "$TARGET"

echo "=> uv"
if ! command -v uv >/dev/null 2>&1; then
  su - "$USER_NAME" -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
  ln -sf "/home/$USER_NAME/.local/bin/uv" /usr/local/bin/uv
fi

echo "=> python deps"
su - "$USER_NAME" -c "cd $TARGET/scraper && uv sync"

echo "=> playwright browsers"
su - "$USER_NAME" -c "cd $TARGET/scraper && uv run python -m playwright install chromium"
"$TARGET/scraper/.venv/bin/python" -m playwright install-deps || true

echo "=> systemd units"
cp "$TARGET/scraper/systemd/"*.service /etc/systemd/system/
cp "$TARGET/scraper/systemd/"*.timer /etc/systemd/system/
systemctl daemon-reload

cat <<EOF
done.

next steps (manual):
  1) cp $TARGET/scraper/config.example.yaml $TARGET/scraper/config.yaml  # then edit
  2) cp $TARGET/scraper/.env.example $TARGET/scraper/.env                 # then edit
  3) tunnel a VNC, run scripts/bootstrap_login.py to log in once
  4) test capture by hand: sudo -u $USER_NAME bash -c 'cd $TARGET/scraper && uv run scraper capture --duration 120'
  5) enable services:
       systemctl enable --now scraper-api
       systemctl enable --now scraper-monitor
       systemctl enable --now scraper-capture
       systemctl enable --now scraper-parse.timer
EOF
