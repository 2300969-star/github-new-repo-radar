#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

python3 -m github_new_repo_radar run \
  --date today \
  --timezone Asia/Shanghai \
  --limit 20 \
  --format all \
  --output-dir ./reports

report_date="$(TZ=Asia/Shanghai date +%F)"
open "./reports/github-new-repos-${report_date}.html"
