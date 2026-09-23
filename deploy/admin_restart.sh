#!/usr/bin/env bash
# =============================================================================
# admin_restart.sh
# Reinicia (o detiene / arranca / consulta) el panel de administración.
#
# Uso:  bash /home/admin_restart.sh [restart|start|stop|status|logs] [ruta] [servicio]
#       sin argumentos = restart
# =============================================================================
set -u

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!]${NC} $1"; }
error()  { echo -e "${RED}[X]${NC} $1"; exit 1; }
header() { echo ""; echo -e "${BLUE}========================================${NC}";
           echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}========================================${NC}"; }

# systemctl list-unit-files | grep -q da falsos negativos con pipefail (grep
# corta la salida y systemctl muere con SIGPIPE), así que se consulta la unidad
# directamente: systemctl cat devuelve != 0 si no existe.
unidad_existe() { systemctl cat "$1.service" >/dev/null 2>&1; }

# Dominio del panel: si escucha sólo en 127.0.0.1, la URL buena es la del vhost.
url_panel() {
    local host dominio esquema
    host="$(grep -oP '"host"\s*:\s*"\K[^"]+' "$PANEL_DIR/config.json" 2>/dev/null)"
    if [ "$host" = "127.0.0.1" ] || [ "$host" = "localhost" ]; then
        for CONF in /etc/apache2/sites-available/panel-admin.conf \
                    /etc/nginx/sites-available/panel-admin.conf; do
            [ -f "$CONF" ] || continue
            dominio="$(grep -oP '^\s*(ServerName|server_name)\s+\K[^;\s]+' "$CONF" | head -1)"
            [ -n "$dominio" ] || continue
            esquema="http"
            [ -d "/etc/letsencrypt/live/$dominio" ] && esquema="https"
            echo "${esquema}://${dominio}   (interno 127.0.0.1:${PUERTO})"
            return
        done
        echo "127.0.0.1:${PUERTO} (sólo local; publícalo con admin_dominio.sh)"
        return
    fi
    echo "http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PUERTO}"
}

ACCION="${1:-restart}"
PANEL_DIR="${2:-${PANEL_DIR:-/opt/integrasolucadminvps}}"
SERVICIO="${3:-${PANEL_SERVICE:-integrasolucadmin}}"

if [ ! -d "$PANEL_DIR" ]; then
    for RUTA in /opt/integrasolucadminvps /home/integrasolucadminvps \
                /root/integrasolucadminvps /home/integrasoluc/integrasolucadminvps; do
        [ -d "$RUTA" ] && PANEL_DIR="$RUTA" && break
    done
fi

PUERTO="$(grep -oP '"port"\s*:\s*\K[0-9]+' "$PANEL_DIR/config.json" 2>/dev/null | head -1)"
PUERTO="${PUERTO:-8600}"

unidad_existe "$SERVICIO" \
    || error "El servicio $SERVICIO no está instalado (ejecuta ./install.sh en $PANEL_DIR)"

mostrar_estado() {
    ESTADO="$(systemctl is-active "$SERVICIO" 2>/dev/null)"
    ARRANQUE="$(systemctl is-enabled "$SERVICIO" 2>/dev/null)"
    DESDE="$(systemctl show "$SERVICIO" -p ExecMainStartTimestamp --value 2>/dev/null)"
    MEM="$(systemctl show "$SERVICIO" -p MemoryCurrent --value 2>/dev/null)"
    [ "$MEM" = "[not set]" ] && MEM=""
    echo -e "  Servicio: ${GREEN}${SERVICIO}${NC}"
    echo -e "  Estado:   $( [ "$ESTADO" = "active" ] && echo -e "${GREEN}${ESTADO}${NC}" || echo -e "${RED}${ESTADO:-desconocido}${NC}" )"
    echo -e "  Arranque: ${ARRANQUE:-desconocido}"
    [ -n "$DESDE" ] && echo -e "  Desde:    ${DESDE}"
    [ -n "$MEM" ] && [ "$MEM" -gt 0 ] 2>/dev/null && echo -e "  Memoria:  $((MEM / 1024 / 1024)) MB"
    echo -e "  Ruta:     ${PANEL_DIR}"
    echo -e "  URL:      $(url_panel)"
}

case "$ACCION" in
    restart|reiniciar)
        header "Reiniciando el panel"
        systemctl restart "$SERVICIO" || error "No se pudo reiniciar $SERVICIO"
        sleep 3
        if [ "$(systemctl is-active "$SERVICIO")" = "active" ]; then
            log "Servicio reiniciado"
        else
            journalctl -u "$SERVICIO" -n 25 --no-pager 2>/dev/null | sed 's/^/      /'
            error "El servicio no quedó activo"
        fi
        mostrar_estado
        if command -v curl >/dev/null 2>&1; then
            RESPUESTA="$(curl -s -m 10 "http://127.0.0.1:${PUERTO}/healthz" 2>/dev/null)"
            echo "$RESPUESTA" | grep -q '"ok"' && log "El panel responde: $RESPUESTA" \
                || warn "El panel todavía no responde en el puerto ${PUERTO}"
        fi
        ;;
    start|iniciar)
        header "Iniciando el panel"
        systemctl start "$SERVICIO" && sleep 2 && log "Iniciado" || error "No se pudo iniciar"
        mostrar_estado
        ;;
    stop|detener)
        header "Deteniendo el panel"
        systemctl stop "$SERVICIO" && log "Detenido" || error "No se pudo detener"
        mostrar_estado
        ;;
    status|estado)
        header "Estado del panel"
        mostrar_estado
        echo ""
        systemctl --no-pager --lines=0 status "$SERVICIO" 2>/dev/null | sed 's/^/  /'
        ;;
    logs|log)
        header "Últimas líneas del log"
        if [ -f "$PANEL_DIR/var/panel.log" ]; then
            tail -n 60 "$PANEL_DIR/var/panel.log"
        else
            journalctl -u "$SERVICIO" -n 60 --no-pager
        fi
        ;;
    *)
        error "Acción desconocida: $ACCION (usa restart, start, stop, status o logs)"
        ;;
esac
echo ""
