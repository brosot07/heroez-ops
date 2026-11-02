#!/usr/bin/env bash
# One-command install of Heroez Ops on a fresh Ubuntu 24.04 server (direct mode: DNS stays where it is,
# Caddy does HTTPS, people log in with a Telegram code). Safe to run again: it updates instead.
#
#   curl -fsSL https://raw.githubusercontent.com/brosot07/heroez-ops/main/deploy/install.sh -o install.sh && bash install.sh
#
# Other addresses:  PUBLIC=ops.example.com SETTINGS=opsadmin.example.com bash install.sh
set -euo pipefail
PUBLIC=${PUBLIC:-heroez.nitsujlabs.app}
SETTINGS=${SETTINGS:-heroezadmin.nitsujlabs.app}
REPO=${REPO:-https://github.com/brosot07/heroez-ops.git}
DIR=/opt/heroez-ops
COMPOSE="docker compose -f deploy/docker-compose.yml"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

if [ "$(id -u)" != "0" ]; then echo "Run as root (the DigitalOcean console logs you in as root)."; exit 1; fi

echo "== 1/5  Updating the server and installing Docker (2-5 minutes)"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
apt-get -y -q install unattended-upgrades ufw git docker.io docker-compose-v2

echo "== 2/5  Firewall: only SSH (22) and web (80, 443) open"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable --now docker >/dev/null

echo "== 3/5  Downloading Heroez Ops"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"

echo "== 4/5  Building the app (3-5 minutes)"
$COMPOSE build -q

echo "== 5/5  First setup and start"
if ! $COMPOSE run --rm -T app sh -c 'test -f /data/config/owner.json -o -f /data/initial_password.txt'; then
  printf '\n%s\n%s\n\n' "$PUBLIC" "$SETTINGS" | $COMPOSE run --rm -T app heroez-ops init > /root/heroez-first-login.txt
  chmod 600 /root/heroez-first-login.txt
fi
$COMPOSE --profile direct up -d

PW=$(grep -o 'First owner password: .*' /root/heroez-first-login.txt 2>/dev/null | cut -d' ' -f4 || true)
echo
echo "=================================================================="
echo " DONE. Heroez Ops is starting (give it 1 minute)."
echo
echo " Settings page : https://$SETTINGS"
echo " Team pages    : https://$PUBLIC/antrian"
if [ -n "$PW" ]; then
  echo " First password: $PW"
  echo "   (copy it now; used once, then you set your own)"
else
  echo " Password      : the one you already set (forgot it? run:"
  echo "   cd $DIR && $COMPOSE exec app heroez-ops reset-password )"
fi
echo "=================================================================="
