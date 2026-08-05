#!/usr/bin/env bash
# Run this ON the VM (as the ubuntu user) after SSHing in for the first time,
# e.g.:
#   ssh -i ~/path/to/key.pem ubuntu@<public-ip>
#   curl -fsSL https://raw.githubusercontent.com/dannybrown37/gtd/main/deploy/oracle/bootstrap.sh | bash
# or copy it over and run it directly.
#
# It installs Tailscale, uv, and gtd-tui[api], then sets up gtd-api as a
# systemd service. It does NOT create ~/.env for you — do that first (see
# below) since it holds secrets this script has no business generating.
set -euo pipefail

if [ ! -f "$HOME/.env" ]; then
  echo "Missing ~/.env — create it first with:" >&2
  echo "  NOTION_PROJECTS_DB_ID=..." >&2
  echo "  NOTION_NOTES_TOKEN=..." >&2
  echo "  GTD_API_KEY=..." >&2
  exit 1
fi
chmod 600 "$HOME/.env"

echo "==> Installing Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
echo "    Follow the printed login URL to join this box to your tailnet."

echo "==> Installing uv"
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "==> Installing gtd-tui[api]"
uv tool install "gtd-tui[api]"

GTD_BIN="$(uv tool dir)/gtd-tui/bin/gtd"
if [ ! -x "$HOME/.local/bin/gtd" ]; then
  echo "Warning: expected gtd at ~/.local/bin/gtd, check 'uv tool list' output." >&2
fi

echo "==> Installing systemd unit"
sudo tee /etc/systemd/system/gtd-api.service > /dev/null <<EOF
[Unit]
Description=GTD API server
After=network.target tailscaled.service
Wants=tailscaled.service

[Service]
Type=simple
User=$USER
EnvironmentFile=$HOME/.env
ExecStart=$HOME/.local/bin/gtd api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now gtd-api

echo "==> Done. Check status with: sudo systemctl status gtd-api"
echo "    Tailscale IP: $(tailscale ip -4 2>/dev/null || echo 'run: tailscale ip -4')"
