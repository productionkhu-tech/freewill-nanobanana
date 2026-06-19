#!/bin/bash
# NanoBanana - double-click launcher for macOS.
# Runs the local web app and opens it in your default browser.
# Keep this Terminal window open while you use the app; close it to quit.
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi
exec "$PY" server_mac.py
