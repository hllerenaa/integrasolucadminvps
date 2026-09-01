# -*- coding: utf-8 -*-
"""Acciones de administración: activar/desactivar servicios y sitios de Apache.

Todo pasa por una lista blanca de acciones y queda registrado en
var/acciones.log con el usuario que la ejecutó.
"""
from __future__ import annotations

import os
import shlex
import shutil

from .utils import ahora_iso, ejecutar

# Acción -> (categoría, plantilla del comando)
# Orden de las unidades en cada acción. Con activación por socket hay que
# tocar el .socket además del .service: si sólo se detiene el servicio, el
# socket sigue escuchando y la primera petición lo vuelve a levantar (es lo
# que hace desactivar_sistemas.sh con stop socket + stop service).
ACCIONES_SERVICIO = {
    'iniciar':      ('start',   ['socket', 'service']),
    'detener':      ('stop',    ['socket', 'service']),
    'reiniciar':    ('restart', ['socket', 'service']),
    'habilitar':    ('enable',  ['socket', 'service']),
    'deshabilitar': ('disable', ['socket', 'service']),
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
        verbo, tipos = ACCIONES_SERVICIO[accion]
        tiene_socket = bool((instancia_datos.get('socket') or {}).get('existe'))
        unidades = []
        for tipo in tipos:
            if tipo == 'socket' and not tiene_socket:
                continue
            unidades.append('%s.%s' % (unidad, tipo))
        return _ejecutar_unidades(config, usuario, accion, verbo, unidades, instancia_datos)
    elif accion in ACCIONES_APACHE:
        web = instancia_datos.get('apache') or {}
        sitio = web.get('sitio')
        if not sitio:
            return {'ok': False, 'error': 'No se detectó el sitio web (vhost) de esta instancia'}
        return _accion_sitio_web(config, usuario, accion, web, sitio)
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

    return resultado


def _accion_sitio_web(config, usuario, accion, web, sitio):
    """Activa o desactiva un sitio, con respaldo si a2ensite/a2dissite falla.

    En nginx (y en Apache cuando el archivo no está en sites-available) se
    gestiona el enlace de sites-enabled directamente, que es lo mismo que
    hacen a2ensite y a2dissite.
    """
    es_nginx = (web.get('servidor') or 'apache') == 'nginx'
    activar = accion == 'apache_activar'
    archivo = web.get('sitio_archivo') or ('%s.conf' % sitio)
    dir_disponibles = web.get('dir_disponibles') or (
        '/etc/nginx/sites-available' if es_nginx else '/etc/apache2/sites-available')
    dir_habilitados = web.get('dir_habilitados') or (
        '/etc/nginx/sites-enabled' if es_nginx else '/etc/apache2/sites-enabled')
    origen = os.path.join(dir_disponibles, archivo)
    enlace = os.path.join(dir_habilitados, archivo)

    pasos = []
    ok = False
    salida_final = ''

    def paso(comando, timeout=60):
        codigo, salida, error = ejecutar(comando, timeout=timeout)
        texto = (salida or error or '').strip()
        pasos.append({'comando': ' '.join(shlex.quote(p) for p in comando),
                      'codigo': codigo, 'salida': texto})
        _registrar(config, usuario, accion, ' '.join(comando), codigo, texto)
        return codigo, texto

    if not es_nginx and shutil.which('a2ensite' if activar else 'a2dissite'):
        codigo, salida_final = paso(['a2ensite' if activar else 'a2dissite', sitio])
        ok = codigo == 0

    if not ok:
        # Respaldo: se maneja el enlace a mano.
        if activar:
            if not os.path.isfile(origen) and os.path.isfile(enlace):
                ok, salida_final = True, 'El archivo ya está en %s' % dir_habilitados
            elif os.path.isfile(origen):
                codigo, salida_final = paso(['ln', '-sfn', origen, enlace])
                ok = codigo == 0
            else:
                salida_final = 'No existe %s' % origen
        else:
            if os.path.islink(enlace):
                codigo, salida_final = paso(['rm', '-f', enlace])
                ok = codigo == 0
            elif os.path.isfile(enlace):
                # Archivo real dentro de sites-enabled: se mueve a
                # sites-available para dejarlo desactivado sin perderlo.
                try:
                    os.makedirs(dir_disponibles, exist_ok=True)
                    shutil.move(enlace, origen)
                    ok = True
                    salida_final = ('El archivo estaba directamente en %s (a2dissite no puede '
                                    'con eso): se movió a %s' % (dir_habilitados, origen))
                    _registrar(config, usuario, accion, enlace, 0, salida_final)
                except OSError as ex:
                    salida_final = 'No se pudo mover %s: %s' % (enlace, ex)
            elif os.path.isfile(origen):
                ok, salida_final = True, 'El sitio ya estaba desactivado'
            else:
                salida_final = 'No se encontró %s ni %s' % (enlace, origen)

    resultado = {'ok': ok, 'accion': accion, 'objetivo': sitio, 'servidor': web.get('servidor'),
                 'comando': ' && '.join(p['comando'] for p in pasos) or '(sin comandos)',
                 'pasos': pasos, 'salida': salida_final}
    if not ok:
        resultado['error'] = salida_final or 'No se pudo cambiar el estado del sitio'
        return resultado

    # Validar y recargar
    demonio = 'nginx' if es_nginx else 'apache2'
    codigo, salida = paso(['nginx', '-t'] if es_nginx else ['apache2ctl', 'configtest'])
    resultado['configtest'] = {'ok': codigo == 0, 'salida': salida}
    if codigo != 0:
        resultado['ok'] = False
        resultado['error'] = ('La configuración de %s no valida, no se recargó: %s'
                              % (demonio, salida))
        return resultado

    codigo, salida = paso(['systemctl', 'reload', demonio])
    resultado['recarga'] = {'ok': codigo == 0, 'salida': salida}
    if codigo != 0:
        resultado['ok'] = False
        resultado['error'] = 'No se pudo recargar %s: %s' % (demonio, salida)
    return resultado


def _ejecutar_unidades(config, usuario, accion, verbo, unidades, instancia_datos):
    """Aplica un verbo de systemctl a varias unidades (socket y servicio)."""
    pasos = []
    ok_global = True
    for unidad in unidades:
        comando = ['systemctl', verbo, unidad]
        codigo, salida, error = ejecutar(comando, timeout=int(config.get('timeout_accion') or 60))
        mensaje = (salida or error or '').strip()
        _registrar(config, usuario, accion, unidad, codigo, mensaje)
        pasos.append({'unidad': unidad, 'ok': codigo == 0, 'codigo': codigo,
                      'comando': ' '.join(shlex.quote(p) for p in comando),
                      'salida': mensaje})
        if codigo != 0:
            ok_global = False

    # Verificación real: tras detener, ni el socket ni el servicio deben quedar
    # escuchando (es justo el caso en que el sitio seguía respondiendo).
    estados = {}
    for unidad in unidades:
        _c, salida, _e = ejecutar(['systemctl', 'is-active', unidad], timeout=20)
        estados[unidad] = (salida or '').strip() or 'desconocido'

    resultado = {
        'ok': ok_global,
        'accion': accion,
        'objetivo': ', '.join(unidades),
        'comando': ' && '.join(p['comando'] for p in pasos),
        'pasos': pasos,
        'estados': estados,
        'salida': '\n'.join('%s: %s' % (u, e) for u, e in estados.items()),
        'codigo': 0 if ok_global else 1,
    }
    if accion == 'detener':
        vivas = [u for u, e in estados.items() if e in ('active', 'listening', 'activating')]
        if vivas:
            resultado['ok'] = False
            resultado['salida'] = ('Siguen activas: %s. Con activación por socket hay que '
                                   'detener también el .socket.' % ', '.join(vivas))
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
