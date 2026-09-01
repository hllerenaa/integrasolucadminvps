#!/usr/bin/env bash
# =============================================================================
# admin_dominio.sh
# Publica el panel en un dominio (detrás de Apache o nginx) en vez de la IP:puerto.
#
#   bash /home/admin_dominio.sh admin.integrasoluc.net
#   bash /home/admin_dominio.sh admin.integrasoluc.net --certbot correo@dominio.com
#
# Qué hace:
#   1. Crea y activa el vhost que hace proxy al panel
#   2. Deja el panel escuchando SÓLO en 127.0.0.1 (ya no se entra por la IP)
#   3. Cierra el puerto en ufw
#   4. Emite el certificado con certbot (con --certbot) y marca la cookie como segura
#
# Opciones: --certbot <correo> | --servidor apache|nginx | --puerto N
#           --mantener-ip (no cambia el bind ni cierra el puerto)
# =============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!]${NC} $1"; }
error()  { echo -e "${RED}[X]${NC} $1"; exit 1; }
header() { echo ""; echo -e "${BLUE}========================================${NC}";
           echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}========================================${NC}"; }

DOMINIO="${1:-}"
[ -n "$DOMINIO" ] || error "Uso: bash $0 <dominio> [--certbot correo] [--servidor apache|nginx]"
shift

SERVIDOR="apache"; CORREO=""; USAR_CERTBOT=0; PUERTO=""; MANTENER_IP=0
PANEL_DIR="${PANEL_DIR:-/opt/integrasolucadminvps}"
SERVICIO="${PANEL_SERVICE:-integrasolucadmin}"

while [ $# -gt 0 ]; do
    case "$1" in
        --certbot) USAR_CERTBOT=1; CORREO="${2:-}"; shift 2 ;;
        --servidor) SERVIDOR="$2"; shift 2 ;;
        --puerto) PUERTO="$2"; shift 2 ;;
        --ruta) PANEL_DIR="$2"; shift 2 ;;
        --servicio) SERVICIO="$2"; shift 2 ;;
        --mantener-ip) MANTENER_IP=1; shift ;;
        *) error "Opción desconocida: $1" ;;
    esac
done

[ "$EUID" -eq 0 ] || error "Ejecútalo con sudo"

if [ ! -d "$PANEL_DIR" ]; then
    for RUTA in /opt/integrasolucadminvps /home/integrasolucadminvps /root/integrasolucadminvps; do
        [ -d "$RUTA" ] && PANEL_DIR="$RUTA" && break
    done
fi
[ -d "$PANEL_DIR" ] || error "No encontré el panel; pásame --ruta /ruta/al/panel"

CONFIG="$PANEL_DIR/config.json"
[ -f "$CONFIG" ] || error "No existe $CONFIG (instala el panel primero)"
[ -z "$PUERTO" ] && PUERTO="$(grep -oP '"port"\s*:\s*\K[0-9]+' "$CONFIG" | head -1)"
PUERTO="${PUERTO:-8600}"

header "Publicando el panel en $DOMINIO"
echo -e "  Panel:    ${GREEN}${PANEL_DIR}${NC} (puerto interno ${PUERTO})"
echo -e "  Servidor: ${GREEN}${SERVIDOR}${NC}"

# --- 1. vhost ----------------------------------------------------------------
header "Paso 1/5: sitio web"
if [ "$SERVIDOR" = "nginx" ]; then
    PLANTILLA="$PANEL_DIR/deploy/plantillas/panel-nginx.conf.tpl"
    DESTINO="/etc/nginx/sites-available/panel-admin.conf"
    sed -e "s|__DOMINIO__|$DOMINIO|g" -e "s|__PUERTO__|$PUERTO|g" "$PLANTILLA" > "$DESTINO"
    ln -sfn "$DESTINO" /etc/nginx/sites-enabled/panel-admin.conf
    nginx -t || error "La configuración de nginx no valida"
    systemctl reload nginx && log "Sitio activo en nginx ($DESTINO)"
else
    PLANTILLA="$PANEL_DIR/deploy/plantillas/panel-apache.conf.tpl"
    DESTINO="/etc/apache2/sites-available/panel-admin.conf"
    sed -e "s|__DOMINIO__|$DOMINIO|g" -e "s|__PUERTO__|$PUERTO|g" "$PLANTILLA" > "$DESTINO"
    a2enmod proxy proxy_http headers >/dev/null 2>&1
    a2ensite panel-admin >/dev/null 2>&1
    apache2ctl configtest || error "La configuración de Apache no valida"
    systemctl reload apache2 && log "Sitio activo en Apache ($DESTINO)"
fi

# --- 2. certbot ---------------------------------------------------------------
header "Paso 2/5: certificado SSL"
CERTBOT_CMD="certbot --${SERVIDOR} -d ${DOMINIO} --redirect --non-interactive --agree-tos"
if [ -n "$CORREO" ]; then
    CERTBOT_CMD="$CERTBOT_CMD -m ${CORREO}"
else
    CERTBOT_CMD="$CERTBOT_CMD --register-unsafely-without-email"
fi

CON_SSL=0
if [ $USAR_CERTBOT -eq 1 ]; then
    command -v certbot >/dev/null 2>&1 || error "certbot no está instalado: apt install certbot python3-certbot-apache"
    if $CERTBOT_CMD; then CON_SSL=1; log "Certificado emitido para $DOMINIO"
    else warn "certbot falló; el panel queda en HTTP. Reintenta a mano:"; echo "      $CERTBOT_CMD"; fi
else
    warn "No se pidió certificado. Para emitirlo ejecuta:"
    echo "      $CERTBOT_CMD"
fi

# --- 3. escuchar sólo en localhost -------------------------------------------
header "Paso 3/5: dejar de salir por la IP"
if [ $MANTENER_IP -eq 1 ]; then
    warn "Se mantiene el acceso por IP:puerto (--mantener-ip)"
else
    cp "$CONFIG" "${CONFIG}.bak-$(date +%Y%m%d_%H%M%S)"
    python3 - "$CONFIG" "$CON_SSL" <<'PYEOF'
import json, os, sys
ruta, con_ssl = sys.argv[1], sys.argv[2] == '1'
with open(ruta, encoding='utf-8') as fh:
    cfg = json.load(fh)
cfg['host'] = '127.0.0.1'          # sólo accesible a través del proxy
if con_ssl:
    cfg['session_cookie_secure'] = True
with open(ruta, 'w', encoding='utf-8') as fh:
    json.dump(cfg, fh, indent=2, ensure_ascii=False)
os.chmod(ruta, 0o600)
PYEOF
    log "config.json: host = 127.0.0.1 (respaldo guardado)"
fi

# --- 4. reiniciar el panel ----------------------------------------------------
header "Paso 4/5: reiniciando el panel"
systemctl restart "$SERVICIO" || error "No se pudo reiniciar $SERVICIO"
sleep 3
[ "$(systemctl is-active "$SERVICIO")" = "active" ] && log "Panel activo" \
    || { journalctl -u "$SERVICIO" -n 20 --no-pager; error "El panel no arrancó"; }

# --- 5. firewall --------------------------------------------------------------
header "Paso 5/5: firewall"
if [ $MANTENER_IP -eq 0 ] && command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw delete allow "${PUERTO}/tcp" >/dev/null 2>&1 && log "Puerto ${PUERTO} cerrado en ufw" \
        || warn "No había regla para el puerto ${PUERTO} en ufw"
else
    warn "Sin cambios en el firewall"
fi

ESQUEMA="http"; [ $CON_SSL -eq 1 ] && ESQUEMA="https"
header "Listo"
echo -e "  Panel:    ${GREEN}${ESQUEMA}://${DOMINIO}${NC}"
echo -e "  Interno:  127.0.0.1:${PUERTO} (ya no responde por la IP pública)"
[ $CON_SSL -eq 0 ] && echo -e "  ${YELLOW}Falta el certificado:${NC} $CERTBOT_CMD"
echo ""
