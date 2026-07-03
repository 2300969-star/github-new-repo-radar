#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root." >&2
  exit 1
fi

PROJECT_DIR="${PROJECT_DIR:-/opt/github-new-repo-radar}"
INSTALL_BIN="/usr/local/bin/github-new-repo-radar-daily"

install -d "$PROJECT_DIR/reports"
install -m 0755 "$PROJECT_DIR/scripts/daily_report.sh" "$INSTALL_BIN"
install -m 0644 "$PROJECT_DIR/deploy/systemd/github-new-repo-radar.service" /etc/systemd/system/github-new-repo-radar.service
install -m 0644 "$PROJECT_DIR/deploy/systemd/github-new-repo-radar.timer" /etc/systemd/system/github-new-repo-radar.timer

systemctl daemon-reload
systemctl enable --now github-new-repo-radar.timer

echo "Installed github-new-repo-radar.timer"
systemctl list-timers github-new-repo-radar.timer --no-pager

