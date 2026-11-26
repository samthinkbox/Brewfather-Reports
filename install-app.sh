#!/bin/sh
# Add Brewfather Reports to your applications menu, and start the server when you sign in (Linux).
# On a Mac this explains how to install it from Chrome or Edge. To remove: python3 app/desktop.py --uninstall
cd "$(dirname "$0")" && exec python3 -I app/desktop.py --install --autostart "$@"
