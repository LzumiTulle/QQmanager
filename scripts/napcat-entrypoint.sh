#!/bin/bash
# Minimal container startup: initialize bundled files and run QQ as the image's napcat user.
# Does not rewrite /proc, /sys, hostnames, or container detection markers.
set -eu
cd /app
if [ ! -f /app/napcat/napcat.mjs ]; then
  mkdir -p /tmp/qqmanager-napcat /app/napcat
  unzip -qo /app/NapCat.Shell.zip -d /tmp/qqmanager-napcat
  for item in /tmp/qqmanager-napcat/*; do
    if [ "$(basename "$item")" != config ]; then
      cp -r "$item" /app/napcat/
    fi
  done
  cp -rn /tmp/qqmanager-napcat/config/. /app/napcat/config/
fi
mkdir -p /app/.config/QQ /app/.cache
chown -R napcat:napcat /app/napcat /app/.config/QQ /app/.cache
ulimit -c 0
gosu napcat Xvfb :1 -screen 0 1080x760x16 +extension GLX +render >/dev/null 2>&1 &
sleep 2
export DISPLAY=:1
cd /app/napcat
if [ -n "${ACCOUNT:-}" ]; then
  exec gosu napcat /opt/QQ/qq --no-sandbox -q "$ACCOUNT"
else
  exec gosu napcat /opt/QQ/qq --no-sandbox
fi
