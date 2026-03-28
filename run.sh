#!/usr/bin/env bash
set -euo pipefail

# penplotter/web — setup & launch script
# Usage: ./run.sh [--no-ssl] [--port PORT]

PORT=4443
USE_SSL=true

while [[ $# -gt 0 ]]; do
  case $1 in
    --no-ssl) USE_SSL=false; shift ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

HOSTNAME=$(hostname -f 2>/dev/null || hostname)

# ---- Python venv ----
if [ ! -d ".venv" ]; then
  echo "→ Creating Python venv..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "→ Installing dependencies..."
pip install -q -r requirements.txt

# ---- SSL certs (self-signed) ----
if [ "$USE_SSL" = true ]; then
  CERT_DIR="$SCRIPT_DIR/certs"
  mkdir -p "$CERT_DIR"
  if [ ! -f "$CERT_DIR/key.pem" ]; then
    echo "→ Generating self-signed certificate..."
    openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout "$CERT_DIR/key.pem" \
      -out "$CERT_DIR/cert.pem" \
      -days 365 \
      -subj "/CN=${HOSTNAME}" \
      -addext "subjectAltName=DNS:${HOSTNAME},DNS:localhost,IP:127.0.0.1" \
      2>/dev/null
    echo "  Certificate created at $CERT_DIR/"
  fi
  export SSL_KEY="$CERT_DIR/key.pem"
  export SSL_CERT="$CERT_DIR/cert.pem"
  PROTO="https"
else
  PROTO="http"
fi

echo ""
echo "  penplotter/web"
echo "  ${PROTO}://${HOSTNAME}:${PORT}"
echo ""

exec python3 server.py
