#!/usr/bin/env bash
# Google Compute Engine startup script for the DarkGlass public demo (Debian 12). It runs on every boot
# and is safe to repeat: install Docker once, fetch the repo once, then start the stack.
# The hostname is the instance's "domain" metadata if set, else <external-ip>.sslip.io, a free DNS name
# that resolves to the IP inside it, so Caddy can get a real HTTPS certificate without buying a domain.
set -euo pipefail
exec > >(tee -a /var/log/darkglass-startup.log) 2>&1
echo "== $(date -Is) DarkGlass startup"

MD=http://metadata.google.internal/computeMetadata/v1
meta() { curl -fs -H "Metadata-Flavor: Google" "$MD/$1" || true; }

if ! command -v docker >/dev/null; then
  apt-get update -q
  apt-get install -yq ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
fi

REPO=$(meta instance/attributes/repo); REPO=${REPO:-https://github.com/soujasK/DarkGlass.git}
DIR=/opt/darkglass
[ -d "$DIR/.git" ] || git clone --depth 1 "$REPO" "$DIR"

DOMAIN=$(meta instance/attributes/domain)
if [ -z "$DOMAIN" ]; then
  IP=$(meta instance/network-interfaces/0/access-configs/0/external-ip)
  DOMAIN="${IP//./-}.sslip.io"
fi
echo "domain: $DOMAIN"

cd "$DIR"
DOMAIN="$DOMAIN" docker compose -f deploy/docker-compose.yml up -d --build
echo "== $(date -Is) DarkGlass is starting at https://$DOMAIN"
