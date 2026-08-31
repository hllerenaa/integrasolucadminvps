#!/usr/bin/env bash
# Instalador del panel de administracion VPS (integrasolucadminvps).
#
#   git clone https://github.com/hllerenaa/integrasolucadminvps /opt/integrasolucadminvps
#   cd /opt/integrasolucadminvps
#   sudo ./install.sh --port 8600 --usuario admin --password "MiClaveSegura"
#
# Es idempotente: se puede volver a ejecutar para actualizar dependencias.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICIO="integrasolucadmin"
PUERTO=8600
USUARIO="admin"
PASSWORD=""
BIND="0.0.0.0"
SIN_SERVICIO=0

uso() {
    cat <<AYUDA
Uso: sudo ./install.sh [opciones]

  --port <n>          Puerto de escucha (por defecto 8600)
  --bind <ip>         Direccion de escucha (por defecto 0.0.0.0 = IP publica)
  --usuario <user>    Usuario del panel (por defecto admin)
  --password <clave>  Clave del panel (si se omite se genera una y se muestra)
  --servicio <nombre> Nombre de la unidad systemd (por defecto integrasolucadmin)
  --sin-servicio      Solo prepara el entorno, no instala systemd
  -h, --help          Muestra esta ayuda
AYUDA
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PUERTO="$2"; shift 2 ;;
        --bind) BIND="$2"; shift 2 ;;
        --usuario) USUARIO="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --servicio) SERVICIO="$2"; shift 2 ;;
        --sin-servicio) SIN_SERVICIO=1; shift ;;
        -h|--help) uso; exit 0 ;;
        *) echo "Opcion desconocida: $1"; uso; exit 1 ;;
    esac
done

echo "==> Instalando panel en $DIR"

if [[ $SIN_SERVICIO -eq 0 && $EUID -ne 0 ]]; then
    echo "!! Ejecutalo con sudo (necesita instalar el servicio systemd y leer /home/*)"
    exit 1
fi

# ------------------------------------------------------------------ python
PY=""
for c in python3.11 python3.10 python3.9 python3.8 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [[ -z "$PY" ]]; then
    echo "!! No se encontro python3 en el servidor"
    exit 1
fi
echo "==> Python: $($PY --version 2>&1)"

if ! "$PY" -c 'import venv' >/dev/null 2>&1; then
    echo "==> Instalando python3-venv"
    (apt-get update -qq && apt-get install -y python3-venv) || {
        echo "!! No se pudo instalar python3-venv automaticamente. Instalalo y reintenta."; exit 1; }
fi

if [[ ! -d "$DIR/venv" ]]; then
    echo "==> Creando entorno virtual"
    "$PY" -m venv "$DIR/venv"
fi

echo "==> Instalando dependencias"
"$DIR/venv/bin/pip" install --upgrade pip -q
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"

mkdir -p "$DIR/var"

# ------------------------------------------------------------------ config
if [[ ! -f "$DIR/config.json" ]]; then
    if [[ -z "$PASSWORD" ]]; then
        PASSWORD="$("$DIR/venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(12))')"
        GENERADA=1
    fi
    echo "==> Creando config.json"
    HASH="$("$DIR/venv/bin/python" -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$PASSWORD")"
    "$DIR/venv/bin/python" - "$DIR" "$BIND" "$PUERTO" "$USUARIO" "$HASH" <<'PYEOF'
import json, os, sys
dir_, bind, puerto, usuario, hash_ = sys.argv[1:6]
with open(os.path.join(dir_, 'config.example.json'), encoding='utf-8') as fh:
    cfg = json.load(fh)
cfg['host'] = bind
cfg['port'] = int(puerto)
cfg['auth']['username'] = usuario
cfg['auth']['password_hash'] = hash_
cfg['auth']['password'] = ''
destino = os.path.join(dir_, 'config.json')
with open(destino, 'w', encoding='utf-8') as fh:
    json.dump(cfg, fh, indent=2, ensure_ascii=False)
os.chmod(destino, 0o600)
PYEOF
else
    echo "==> config.json ya existe, se conserva (edita el archivo para cambiar puerto o clave)"
    PUERTO="$("$DIR/venv/bin/python" -c "import json;print(json.load(open('$DIR/config.json'))['port'])")"
fi

# ---------------------------------------------------------------- systemd
if [[ $SIN_SERVICIO -eq 0 ]]; then
    echo "==> Instalando servicio systemd: $SERVICIO"
    sed "s|__DIR__|$DIR|g" "$DIR/deploy/integrasolucadmin.service" > "/etc/systemd/system/${SERVICIO}.service"
    systemctl daemon-reload
    systemctl enable "$SERVICIO" >/dev/null 2>&1 || true
    systemctl restart "$SERVICIO"
    sleep 2
    systemctl --no-pager --lines=0 status "$SERVICIO" || true
fi

# ---------------------------------------------------------------- firewall
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    echo "==> Abriendo el puerto $PUERTO en ufw"
    ufw allow "${PUERTO}/tcp" >/dev/null 2>&1 || true
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "==============================================="
echo " Panel instalado"
echo " URL local:   http://${IP:-127.0.0.1}:${PUERTO}"
echo " URL publica: http://<IP-PUBLICA>:${PUERTO}"
echo " Usuario:     ${USUARIO}"
if [[ "${GENERADA:-0}" == "1" ]]; then
    echo " Clave:       ${PASSWORD}   <-- guardala, no se vuelve a mostrar"
elif [[ -n "$PASSWORD" ]]; then
    echo " Clave:       la indicada en --password"
fi
echo
echo " Servicio:    systemctl status ${SERVICIO}"
echo " Logs:        tail -f ${DIR}/var/panel.log"
echo " Reporte CLI: ${DIR}/venv/bin/python ${DIR}/run.py --reporte"
echo "==============================================="
