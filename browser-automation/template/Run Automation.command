#!/bin/bash
# Double-click to run this browser automation (macOS).
# The first run sets up a private Python environment in .venv (a few minutes).
cd "$(dirname "$0")" || exit 1

SCHEDULED=0
for a in "$@"; do [ "$a" = "--scheduled" ] && SCHEDULED=1; done
finish() {
  if [ $SCHEDULED -eq 0 ]; then echo; read -r -p "Press Enter to close this window..." _; fi
  exit "$1"
}

REQ_HASH="$(shasum requirements.txt | cut -d' ' -f1)"
if [ ! -f .venv/.ready ] || [ "$(cat .venv/.ready)" != "$REQ_HASH" ]; then
  echo "Setting things up (first run only, takes a few minutes)..."
  PY=""
  for c in python3.12 python3.11 python3.13 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/ then try again."
    finish 1
  fi
  [ -x .venv/bin/python ] || "$PY" -m venv .venv || { echo "Could not create .venv"; finish 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt || { echo "Installing packages failed (check internet)."; finish 1; }
  .venv/bin/python -m playwright install chromium || { echo "Downloading the browser failed (check internet)."; finish 1; }
  echo "$REQ_HASH" > .venv/.ready
  echo "Setup complete."
fi

.venv/bin/python run.py "$@"
finish $?
