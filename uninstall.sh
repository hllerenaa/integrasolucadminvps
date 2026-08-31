#!/usr/bin/env bash
# Desinstala el servicio del panel (no borra el codigo ni config.json).
set -euo pipefail
SERVICIO="${1:-integrasolucadmin}"

if [[ $EUID -ne 0 ]]; then echo "!! Ejecutalo con sudo"; exit 1; fi

systemctl stop "$SERVICIO" 2>/dev/null || true
systemctl disable "$SERVICIO" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICIO}.service"
systemctl daemon-reload
echo "==> Servicio $SERVICIO eliminado. El codigo y config.json siguen en disco."
