#!/usr/bin/env bash
# Run a Python module with the project's virtualenv, whatever shell or IDE started the commit.
set -e
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else echo "No .venv found: run make install first" >&2; exit 1; fi
exec "$PY" "$@"
