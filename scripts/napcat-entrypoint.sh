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
display_file=$(mktemp)
gosu napcat Xvfb -displayfd 3 -screen 0 1080x760x16 +extension GLX +render 3>"$display_file" >/dev/null &
xvfb_pid=$!
for ((i=0; i<100; i++)); do
  [ -s "$display_file" ] && break
  kill -0 "$xvfb_pid" || exit 1
  sleep 0.1
done
read -r display_number < "$display_file"
rm -f "$display_file"
export DISPLAY=":$display_number"
cd /app/napcat
if [ -z "${ACCOUNT:-}" ] && [ -r /app/napcat/config/qqmanager-account ]; then
  ACCOUNT=$(cat /app/napcat/config/qqmanager-account)
  [[ "$ACCOUNT" =~ ^[0-9]{5,20}$ ]] || ACCOUNT=''
fi
if [ -n "${ACCOUNT:-}" ]; then
  exec gosu napcat /opt/QQ/qq --no-sandbox -q "$ACCOUNT"
else
  exec gosu napcat /opt/QQ/qq --no-sandbox
fi
