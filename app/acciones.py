# -*- coding: utf-8 -*-
"""Acciones de administración: activar/desactivar servicios y sitios de Apache.

Todo pasa por una lista blanca de acciones y queda registrado en
var/acciones.log con el usuario que la ejecutó.
"""
from __future__ import annotations

import os
import shlex

from .utils import ahora_iso, ejecutar

# Acción -> (categoría, plantilla del comando)
ACCIONES_SERVICIO = {
    'iniciar':   ['systemctl', 'start', '{unidad}'],
    'detener':   ['systemctl', 'stop', '{unidad}'],
    'reiniciar': ['systemctl', 'restart', '{unidad}'],
    'habilitar': ['systemctl', 'enable', '{unidad}'],
    'deshabilitar': ['systemctl', 'disable', '{unidad}'],
}

# Sitios web: el comando depende de si la instancia está en Apache o en nginx.
ACCIONES_APACHE = ('apache_activar', 'apache_desactivar')

ACCIONES = sorted(list(ACCIONES_SERVICIO) + list(ACCIONES_APACHE) + ['apache_recargar'])


def _registrar(config, usuario, accion, objetivo, codigo, salida):
    ruta = os.path.join(config.var_dir, 'acciones.log')
    linea = '%s\t%s\t%s\t%s\trc=%s\t%s\n' % (
        ahora_iso(), usuario or '-', accion, objetivo, codigo,
        (salida or '').replace('\n', ' ')[:400])
    try:
        with open(ruta, 'a', encoding='utf-8') as fh:
            fh.write(linea)
    except OSError:
        pass


def registrar_evento(config, usuario, accion, objetivo, codigo=0, detalle=''):
    """Deja constancia en el log de acciones de algo que no es un comando."""
    _registrar(config, usuario, accion, objetivo, codigo, detalle)


def permitida(config, accion):
    cfg = config.get('acciones') or {}
    if not cfg.get('enabled', True):
        return False, 'Las acciones están desactivadas en config.json'
    if accion in ACCIONES_SERVICIO and not cfg.get('servicios', True):
        return False, 'Las acciones sobre servicios están desactivadas'
    if (accion in ACCIONES_APACHE or accion == 'apache_recargar') and not cfg.get('apache', True):
        return False, 'Las acciones sobre Apache están desactivadas'
    if accion not in ACCIONES:
        return False, 'Acción no permitida: %s' % accion
    return True, None


def ejecutar_accion(config, instancia_datos, accion, usuario=None):
    """Ejecuta una acción sobre una instancia ya recolectada.

    instancia_datos es el dict que produce el colector (trae servicio y apache).
    """
    ok, motivo = permitida(config, accion)
    if not ok:
        return {'ok': False, 'error': motivo}

    if accion in ACCIONES_SERVICIO:
        unidad = instancia_datos.get('servicio')
        if not unidad:
            return {'ok': False, 'error': 'La instancia no tiene servicio systemd asociado'}
        comando = [p.format(unidad=unidad) for p in ACCIONES_SERVICIO[accion]]
        objetivo = unidad
    elif accion in ACCIONES_APACHE:
        web = instancia_datos.get('apache') or {}
        sitio = web.get('sitio')
        if not sitio:
            return {'ok': False, 'error': 'No se detectó el sitio web (vhost) de esta instancia'}
        if (web.get('servidor') or 'apache') == 'nginx':
            enlace = os.path.join(web.get('dir_habilitados') or '/etc/nginx/sites-enabled',
                                  web.get('sitio_archivo') or sitio)
            origen = os.path.join(web.get('dir_disponibles') or '/etc/nginx/sites-available',
                                  web.get('sitio_archivo') or sitio)
            if accion == 'apache_activar':
                comando = ['ln', '-sfn', origen, enlace]
            else:
                comando = ['rm', '-f', enlace]
        else:
            comando = ['a2ensite' if accion == 'apache_activar' else 'a2dissite', sitio]
        objetivo = sitio
    else:  # apache_recargar
        servidor = ((instancia_datos.get('apache') or {}).get('servidor') or 'apache')
        comando = ['systemctl', 'reload', 'nginx' if servidor == 'nginx' else 'apache2']
        objetivo = comando[-1]

    codigo, salida, error = ejecutar(comando, timeout=int(config.get('timeout_accion') or 60))
    mensaje = salida or error or ''
    _registrar(config, usuario, accion, objetivo, codigo, mensaje)

    resultado = {
        'ok': codigo == 0,
        'accion': accion,
        'objetivo': objetivo,
        'comando': ' '.join(shlex.quote(p) for p in comando),
        'salida': mensaje.strip(),
        'codigo': codigo,
    }

    # Activar o desactivar un sitio sólo surte efecto tras recargar el servidor.
    if resultado['ok'] and accion in ACCIONES_APACHE:
        es_nginx = ((instancia_datos.get('apache') or {}).get('servidor') or 'apache') == 'nginx'
        demonio = 'nginx' if es_nginx else 'apache2'
        # Antes de recargar se valida la configuración para no tumbar el servidor.
        prueba = ['nginx', '-t'] if es_nginx else ['apache2ctl', 'configtest']
        codigo_prueba, salida_prueba, error_prueba = ejecutar(prueba, timeout=60)
        resultado['configtest'] = {'ok': codigo_prueba == 0,
                                   'salida': (salida_prueba or error_prueba or '').strip()}
        if codigo_prueba != 0:
            resultado['ok'] = False
            resultado['salida'] = ('La configuración de %s no valida, no se recargó: %s'
                                   % (demonio, (salida_prueba or error_prueba or '').strip()))
            _registrar(config, usuario, 'configtest', demonio, codigo_prueba,
                       salida_prueba or error_prueba)
            return resultado
        codigo2, salida2, error2 = ejecutar(['systemctl', 'reload', demonio], timeout=60)
        resultado['recarga'] = {'ok': codigo2 == 0, 'salida': (salida2 or error2 or '').strip()}
        _registrar(config, usuario, 'recargar_web', demonio, codigo2, salida2 or error2)

    return resultado


def historial(config, limite=100):
    """Últimas acciones ejecutadas desde el panel."""
    ruta = os.path.join(config.var_dir, 'acciones.log')
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, 'r', encoding='utf-8', errors='replace') as fh:
            lineas = fh.readlines()[-limite:]
    except OSError:
        return []
    salida = []
    for linea in reversed(lineas):
        partes = linea.rstrip('\n').split('\t')
        if len(partes) >= 5:
            salida.append({
                'fecha': partes[0], 'usuario': partes[1], 'accion': partes[2],
                'objetivo': partes[3], 'resultado': partes[4],
                'salida': partes[5] if len(partes) > 5 else '',
            })
    return salida
