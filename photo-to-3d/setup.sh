#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PY=$(command -v python3.11 || command -v python3)
"$PY" -m venv .venv
.venv/bin/python install.py
