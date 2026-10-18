#!/usr/bin/env bash
# Google Compute Engine startup script for the DarkGlass public demo (Debian 12). It runs on every boot
# and is safe to repeat: install Docker once, fetch or update the repo, then start the stack.
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
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone --depth 1 "$REPO" "$DIR"; fi

DOMAIN=$(meta instance/attributes/domain)
if [ -z "$DOMAIN" ]; then
  IP=$(meta instance/network-interfaces/0/access-configs/0/external-ip)
  DOMAIN="${IP//./-}.sslip.io"
fi
echo "domain: $DOMAIN"

# Gemini: on only when the instance metadata says llm=true. It then uses this project's Vertex AI through the
# machine's service account (needs the cloud-platform scope and the Vertex AI User role); no key is stored.
LLM_ENABLED=$(meta instance/attributes/llm); LLM_ENABLED=${LLM_ENABLED:-false}
VERTEX_PROJECT_ID=""
if [ "$LLM_ENABLED" = "true" ]; then VERTEX_PROJECT_ID=$(meta project/project-id); fi
echo "gemini: $LLM_ENABLED"

cd "$DIR"
DOMAIN="$DOMAIN" LLM_ENABLED="$LLM_ENABLED" VERTEX_PROJECT_ID="$VERTEX_PROJECT_ID" docker compose -f deploy/docker-compose.yml up -d --build
echo "== $(date -Is) DarkGlass is starting at https://$DOMAIN"
