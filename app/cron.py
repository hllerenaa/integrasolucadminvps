# -*- coding: utf-8 -*-
"""Tareas programadas del servidor: crontabs y temporizadores de systemd.

Sólo lectura. Se juntan cuatro orígenes, que es donde acaban las tareas que
se programan en estos servidores:

  - /var/spool/cron/crontabs/<usuario>  (lo que deja `crontab -e`)
  - /etc/crontab y /etc/cron.d/*        (llevan una columna de usuario extra)
  - /etc/cron.{hourly,daily,weekly,monthly}/*  (scripts sueltos, sin horario)
  - los .timer de systemd, que es como Debian renueva los certificados

El horario se traduce a una frase en castellano para no tener que leer los
cinco campos del cron a ojo.
"""
from __future__ import annotations

import os
import re
import subprocess
import time

DIR_SPOOL = '/var/spool/cron/crontabs'
ARCHIVO_SISTEMA = '/etc/crontab'
DIR_CRON_D = '/etc/cron.d'
DIRS_PERIODICOS = (
    ('/etc/cron.hourly', 'cada hora'),
    ('/etc/cron.daily', 'todos los días'),
    ('/etc/cron.weekly', 'cada semana'),
    ('/etc/cron.monthly', 'cada mes'),
)

# Una línea de asignación (PATH=..., MAILTO=...) no es una tarea.
_RE_ASIGNACION = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*=')

ATAJOS = {
    '@reboot': 'al arrancar el servidor',
    '@yearly': 'una vez al año',
    '@annually': 'una vez al año',
    '@monthly': 'el día 1 de cada mes',
    '@weekly': 'los domingos',
    '@daily': 'todos los días a medianoche',
    '@midnight': 'todos los días a medianoche',
    '@hourly': 'cada hora en punto',
}

DIAS = {'0': 'domingo', '1': 'lunes', '2': 'martes', '3': 'miércoles',
        '4': 'jueves', '5': 'viernes', '6': 'sábado', '7': 'domingo',
        'sun': 'domingo', 'mon': 'lunes', 'tue': 'martes', 'wed': 'miércoles',
        'thu': 'jueves', 'fri': 'viernes', 'sat': 'sábado'}


def _hora(minuto, hora):
    """'30 3' -> '03:30' cuando ambos son un número suelto."""
    if minuto.isdigit() and hora.isdigit():
        return '%02d:%02d' % (int(hora), int(minuto))
    return None


def _plural(dia):
    """domingo -> domingos; lunes -> lunes (los que ya acaban en s no cambian)."""
    return dia + 's' if dia.endswith('o') else dia


def _dias_semana(campo):
    """Devuelve la frase completa: 'los domingos', 'de lunes a viernes'."""
    tramos, sueltos = [], []
    for parte in campo.split(','):
        if '-' in parte:
            desde, hasta = parte.split('-', 1)
            desde, hasta = DIAS.get(desde.lower()), DIAS.get(hasta.lower())
            if not desde or not hasta:
                return None
            tramos.append('de %s a %s' % (desde, hasta))
        else:
            nombre = DIAS.get(parte.lower())
            if not nombre:
                return None
            sueltos.append(_plural(nombre))
    if sueltos:
        # "lunes, miércoles y viernes" en vez de encadenar todo con "y".
        lista = (', '.join(sueltos[:-1]) + ' y ' + sueltos[-1]) if len(sueltos) > 1 else sueltos[0]
        tramos.append('los ' + lista)
    return ' y '.join(tramos)


def describir(expresion):
    """Traduce el horario de cron a una frase corta en castellano."""
    expresion = (expresion or '').strip()
    if expresion in ATAJOS:
        return ATAJOS[expresion]
    campos = expresion.split()
    if len(campos) != 5:
        return expresion
    minuto, hora, dia_mes, mes, dia_semana = campos

    if minuto.startswith('*/') and hora == '*':
        cada = 'cada %s minutos' % minuto[2:]
    elif hora.startswith('*/'):
        cada = 'cada %s horas' % hora[2:]
        if minuto.isdigit() and int(minuto):
            cada += ' (en el minuto %s)' % minuto
    elif minuto == '*' and hora == '*':
        cada = 'cada minuto'
    elif hora == '*' and minuto.isdigit():
        cada = 'cada hora, en el minuto %s' % int(minuto)
    else:
        reloj = _hora(minuto, hora)
        cada = 'a las %s' % reloj if reloj else expresion
        if not reloj:
            return expresion

    cuando = []
    if dia_semana != '*':
        nombres = _dias_semana(dia_semana)
        cuando.append(nombres or 'los días %s de la semana' % dia_semana)
    if dia_mes != '*':
        cuando.append('el día %s del mes' % dia_mes)
    if mes != '*':
        cuando.append('en el mes %s' % mes)
    if not cuando and not cada.startswith('cada'):
        cuando.append('todos los días')
    return ' '.join(cuando + [cada]) if cuando else cada


def _leer(ruta):
    try:
        with open(ruta, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read().splitlines(), None
    except OSError as ex:
        return [], str(ex)


def _entradas_de(lineas, origen, usuario=None, con_usuario=False):
    """Convierte las líneas de un crontab en entradas.

    `con_usuario` distingue los archivos de /etc (que llevan una columna de
    usuario entre el horario y el comando) de los crontabs personales.
    """
    entradas = []
    for numero, linea in enumerate(lineas, start=1):
        texto = linea.strip()
        if not texto or texto.startswith('#') or _RE_ASIGNACION.match(texto):
            continue
        if texto.startswith('@'):
            partes = texto.split(None, 2 if con_usuario else 1)
            expresion = partes[0]
            resto = partes[1:]
        else:
            partes = texto.split(None, 6 if con_usuario else 5)
            if len(partes) < (7 if con_usuario else 6):
                continue
            expresion = ' '.join(partes[:5])
            resto = partes[5:]
        if con_usuario:
            duenio = resto[0] if resto else (usuario or 'root')
            comando = resto[1] if len(resto) > 1 else ''
        else:
            duenio = usuario
            comando = resto[0] if resto else ''
        if not comando:
            continue
        entradas.append({
            'origen': origen,
            'usuario': duenio,
            'expresion': expresion,
            'descripcion': describir(expresion),
            'comando': comando,
            'linea': numero,
        })
    return entradas


def _ejecutar(orden):
    try:
        proceso = subprocess.run(orden, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=15)
    except (OSError, subprocess.SubprocessError) as ex:
        return None, str(ex)
    salida = (proceso.stdout or b'').decode('utf-8', 'replace')
    if proceso.returncode != 0:
        return None, salida.strip()[:300]
    return salida, None


def _fecha_us(valor):
    """Microsegundos desde epoch, como los da systemctl show, a texto."""
    try:
        us = int(valor)
    except (TypeError, ValueError):
        return None
    if us <= 0:
        return None
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(us / 1000000.0))


def _timers(config):
    """Temporizadores de systemd (certbot renueva así en Debian).

    Las columnas de `list-timers` no se pueden partir a ojo (el tiempo
    restante ocupa un número variable de palabras), así que de ahí sólo se
    saca la lista de unidades y las fechas se piden con `show`, que las da
    en microsegundos.
    """
    if not config.get('cron_systemd', True):
        return [], None
    salida, error = _ejecutar(['systemctl', 'list-timers', '--all', '--no-pager', '--no-legend'])
    if error:
        return [], error
    unidades = []
    for linea in salida.splitlines():
        for campo in linea.split():
            if campo.endswith('.timer'):
                unidades.append(campo)
                break
    if not unidades:
        return [], None

    detalle, error = _ejecutar(['systemctl', 'show', '--no-pager',
                                '--property=Id', '--property=Description',
                                '--property=Unit', '--property=NextElapseUSecRealtime',
                                '--property=LastTriggerUSec'] + unidades)
    propiedades, actual = [], {}
    for linea in (detalle or '').splitlines():
        if not linea.strip():
            if actual:
                propiedades.append(actual)
                actual = {}
            continue
        if '=' in linea:
            clave, valor = linea.split('=', 1)
            actual[clave] = valor
    if actual:
        propiedades.append(actual)
    por_id = {p.get('Id'): p for p in propiedades if p.get('Id')}

    timers = []
    for unidad in unidades:
        p = por_id.get(unidad, {})
        timers.append({
            'unidad': unidad,
            'descripcion': p.get('Description') or '',
            'activa': p.get('Unit') or '',
            'proxima': _fecha_us(p.get('NextElapseUSecRealtime')),
            'ultima': _fecha_us(p.get('LastTriggerUSec')),
        })
    return timers, None


def listar(config):
    """Todas las tareas programadas que el servidor tiene configuradas."""
    spool = config.get('cron_spool') or DIR_SPOOL
    sistema = config.get('cron_sistema') or ARCHIVO_SISTEMA
    cron_d = config.get('cron_d') or DIR_CRON_D

    entradas, errores = [], []

    # Crontabs personales: el propio directorio es la lista de usuarios que
    # tienen uno, así que no hace falta recorrer /etc/passwd.
    try:
        usuarios = sorted(os.listdir(spool))
    except OSError as ex:
        usuarios = []
        if os.path.isdir(os.path.dirname(spool)):
            errores.append('%s: %s' % (spool, ex))
    for usuario in usuarios:
        ruta = os.path.join(spool, usuario)
        if not os.path.isfile(ruta):
            continue
        lineas, error = _leer(ruta)
        if error:
            errores.append('%s: %s' % (ruta, error))
        entradas.extend(_entradas_de(lineas, 'crontab de %s' % usuario, usuario=usuario))

    if os.path.isfile(sistema):
        lineas, error = _leer(sistema)
        if error:
            errores.append('%s: %s' % (sistema, error))
        entradas.extend(_entradas_de(lineas, sistema, con_usuario=True))

    if os.path.isdir(cron_d):
        for nombre in sorted(os.listdir(cron_d)):
            ruta = os.path.join(cron_d, nombre)
            # cron ignora los archivos con punto o con extensiones de copia.
            if not os.path.isfile(ruta) or '.' in nombre:
                continue
            lineas, error = _leer(ruta)
            if error:
                errores.append('%s: %s' % (ruta, error))
            entradas.extend(_entradas_de(lineas, ruta, con_usuario=True))

    periodicos = []
    for carpeta, cuando in DIRS_PERIODICOS:
        if not os.path.isdir(carpeta):
            continue
        for nombre in sorted(os.listdir(carpeta)):
            ruta = os.path.join(carpeta, nombre)
            if os.path.isfile(ruta) and os.access(ruta, os.X_OK):
                periodicos.append({'origen': carpeta, 'descripcion': cuando,
                                   'comando': ruta, 'usuario': 'root',
                                   'expresion': '', 'linea': 0})
    entradas.extend(periodicos)

    timers, error_timers = _timers(config)
    if error_timers:
        errores.append('systemctl list-timers: %s' % error_timers)

    return {
        'entradas': entradas,
        'timers': timers,
        'errores': errores,
        'total': len(entradas),
    }
