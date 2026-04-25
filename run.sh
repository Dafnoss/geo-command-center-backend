#!/usr/bin/env bash
# Convenience launcher for the GEO Command Center backend.
set -euo pipefail

cd "$(dirname "$0")"

# 1. Create virtualenv on first run
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# 2. Install/refresh dependencies
. .venv/bin/activate
pip install --quiet -r requirements.txt

# 3. Copy .env.example → .env if missing
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env — edit it to add OPENAI_API_KEY for AI recommendation generation."
fi

# 4. Boot uvicorn (reload mode for development)
exec uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
