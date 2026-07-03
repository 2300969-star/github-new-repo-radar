#!/bin/sh
set -eu

OUTPUT_DIR="${GITHUB_RADAR_OUTPUT_DIR:-/opt/github-new-repo-radar/reports}"
LIMIT="${GITHUB_RADAR_LIMIT:-20}"
TIMEZONE="${GITHUB_RADAR_TIMEZONE:-Asia/Shanghai}"
PAGES="${GITHUB_RADAR_PAGES:-2}"
PAGE_DELAY="${GITHUB_RADAR_PAGE_DELAY:-2.0}"

mkdir -p "$OUTPUT_DIR"

github-new-repo-radar run \
  --date today \
  --timezone "$TIMEZONE" \
  --limit "$LIMIT" \
  --pages "$PAGES" \
  --page-delay "$PAGE_DELAY" \
  --format all \
  --output-dir "$OUTPUT_DIR" \
  --summary-file "$OUTPUT_DIR/latest-summary.md"
