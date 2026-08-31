#!/usr/bin/env bash
# Actualiza el panel: git pull + dependencias + reinicio del servicio.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICIO="${1:-integrasolucadmin}"

echo "==> git pull en $DIR"
git -C "$DIR" pull --ff-only

if [[ -x "$DIR/venv/bin/pip" ]]; then
    echo "==> Actualizando dependencias"
    "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
fi

if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICIO}.service"; then
    echo "==> Reiniciando $SERVICIO"
    systemctl restart "$SERVICIO"
    sleep 2
    systemctl --no-pager --lines=0 status "$SERVICIO" || true
else
    echo "!! El servicio $SERVICIO no esta instalado (usa ./install.sh)"
fi
