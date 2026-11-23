#!/usr/bin/env bash
# Starts the finance app. Settings come from .env (never committed).
cd "$(dirname "$0")" || exit 1
set -a; . ./.env; set +a
exec python3 -m src.app
