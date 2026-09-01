#!/usr/bin/env bash
# =============================================================================
# admin_update.sh
# Actualiza el panel de administración (integrasolucadminvps):
#   git pull  ->  dependencias  ->  reinicio del servicio  ->  verificación
#
# Uso:  bash /home/admin_update.sh [ruta_del_proyecto] [nombre_del_servicio]
# =============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!]${NC} $1"; }
error()  { echo -e "${RED}[X]${NC} $1"; exit 1; }
header() { echo ""; echo -e "${BLUE}========================================${NC}";
           echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}========================================${NC}"; }

# --- Ruta del proyecto y servicio --------------------------------------------
PANEL_DIR="${1:-${PANEL_DIR:-/opt/integrasolucadminvps}}"
SERVICIO="${2:-${PANEL_SERVICE:-integrasolucadmin}}"

if [ ! -d "$PANEL_DIR" ]; then
    for RUTA in /opt/integrasolucadminvps /home/integrasolucadminvps \
                /root/integrasolucadminvps /home/integrasoluc/integrasolucadminvps; do
        [ -d "$RUTA" ] && PANEL_DIR="$RUTA" && break
    done
fi
[ -d "$PANEL_DIR" ] || error "No encontré el proyecto. Pásame la ruta: bash $0 /ruta/al/panel"
[ -d "$PANEL_DIR/.git" ] || error "$PANEL_DIR no es un repositorio git"

header "Actualizando el panel"
echo -e "  Ruta:     ${GREEN}${PANEL_DIR}${NC}"
echo -e "  Servicio: ${GREEN}${SERVICIO}${NC}"

cd "$PANEL_DIR" || error "No se pudo entrar a $PANEL_DIR"

# --- Aviso si hay cambios locales --------------------------------------------
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    warn "Hay cambios locales sin commitear (config.json y excluidos.txt están ignorados, no estorban):"
    git status --short --untracked-files=no | sed 's/^/      /'
fi

ANTES="$(git rev-parse --short HEAD 2>/dev/null)"

# --- git pull con reintentos --------------------------------------------------
header "Paso 1/4: git pull"
INTENTO=1; ESPERA=2; OK=0
while [ $INTENTO -le 4 ]; do
    if git pull --ff-only; then OK=1; break; fi
    warn "Falló el git pull (intento ${INTENTO}/4). Reintento en ${ESPERA}s..."
    sleep $ESPERA; ESPERA=$((ESPERA * 2)); INTENTO=$((INTENTO + 1))
done
[ $OK -eq 1 ] || error "No se pudo actualizar el código desde git"

DESPUES="$(git rev-parse --short HEAD 2>/dev/null)"
if [ "$ANTES" = "$DESPUES" ]; then
    log "Ya estaba al día ($DESPUES)"
else
    log "Actualizado: $ANTES -> $DESPUES"
    git --no-pager log --oneline "${ANTES}..${DESPUES}" | sed 's/^/      /'
fi

# --- Dependencias -------------------------------------------------------------
header "Paso 2/4: dependencias"
if [ -x "$PANEL_DIR/venv/bin/pip" ]; then
    "$PANEL_DIR/venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1
    if "$PANEL_DIR/venv/bin/pip" install -q -r "$PANEL_DIR/requirements.txt"; then
        log "Dependencias al día"
    else
        error "Falló la instalación de dependencias"
    fi
else
    warn "No hay venv en $PANEL_DIR/venv; ejecuta primero: sudo ./install.sh"
fi

# --- Reinicio -----------------------------------------------------------------
header "Paso 3/4: reiniciando $SERVICIO"
if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICIO}.service"; then
    systemctl restart "$SERVICIO" || error "No se pudo reiniciar $SERVICIO"
    sleep 3
    ESTADO="$(systemctl is-active "$SERVICIO" 2>/dev/null)"
    if [ "$ESTADO" = "active" ]; then
        log "Servicio activo"
    else
        echo ""; journalctl -u "$SERVICIO" -n 25 --no-pager 2>/dev/null | sed 's/^/      /'
        error "El servicio quedó en estado: ${ESTADO:-desconocido}"
    fi
else
    warn "El servicio $SERVICIO no está instalado en systemd (usa ./install.sh)"
fi

# --- Verificación -------------------------------------------------------------
header "Paso 4/4: verificación"
PUERTO="$(grep -oP '"port"\s*:\s*\K[0-9]+' "$PANEL_DIR/config.json" 2>/dev/null | head -1)"
PUERTO="${PUERTO:-8600}"
if command -v curl >/dev/null 2>&1; then
    RESPUESTA="$(curl -s -m 10 "http://127.0.0.1:${PUERTO}/healthz" 2>/dev/null)"
    if echo "$RESPUESTA" | grep -q '"ok"'; then
        log "El panel responde en el puerto ${PUERTO}"
        echo "      $RESPUESTA"
    else
        warn "El panel no respondió en http://127.0.0.1:${PUERTO}/healthz"
        journalctl -u "$SERVICIO" -n 15 --no-pager 2>/dev/null | sed 's/^/      /'
    fi
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
header "Listo"
echo -e "  Versión:  ${GREEN}${DESPUES}${NC}"
echo -e "  URL:      ${GREEN}http://${IP:-127.0.0.1}:${PUERTO}${NC}"
echo -e "  Logs:     tail -f ${PANEL_DIR}/var/panel.log"
echo ""
