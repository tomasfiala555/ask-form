#!/bin/sh
# Runs ask_form.py with whichever Python actually works (python3 on Linux/WSL,
# python on Windows Git Bash, where python3 is often the Microsoft Store stub).
DIR=$(cd "$(dirname "$0")" && pwd)
for py in python3 python py; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c "import sys; sys.exit(sys.version_info < (3, 8))" >/dev/null 2>&1; then
    exec "$py" "$DIR/ask_form.py" "$@"
  fi
done
echo "ask-form: Python 3.8+ not found" >&2
exit 1
