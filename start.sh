#!/bin/sh
# Start Brewfather Reports, then open http://localhost:8765/admin.html
cd "$(dirname "$0")" && exec python3 app/server.py "$@"
