# -*- coding: utf-8 -*-
"""Consulta del estado de los servicios systemd de cada instancia."""
from __future__ import annotations

import datetime
import os
import re

from .utils import bytes_legible, duracion_legible, ejecutar

PROPIEDADES = (
    'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'MainPID',
    'MemoryCurrent', 'NRestarts', 'ExecMainStartTimestamp',
    'ActiveEnterTimestamp', 'StateChangeTimestamp', 'FragmentPath', 'Description',
)

_RE_PUERTO = re.compile(r'(?:--bind|-b|--bind=|runserver)[= ]+(?:[\w\.\*]*:)?(\d{2,5})')


def _parsear_fecha(texto):
    """Convierte 'Wed 2026-08-27 10:11:12 -05' en datetime (o None)."""
    if not texto or texto in ('n/a', '0'):
        return None
    for formato in ('%a %Y-%m-%d %H:%M:%S %Z', '%a %Y-%m-%d %H:%M:%S %z', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.datetime.strptime(texto.strip(), formato)
        except ValueError:
            continue
    # Último intento: quitar el día de la semana y la zona horaria.
    partes = texto.split()
    if len(partes) >= 3:
        try:
            return datetime.datetime.strptime('%s %s' % (partes[1], partes[2]), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    return None


def _puerto_del_unit(fragment_path):
    """Intenta deducir el puerto en el que escucha el servicio leyendo su unit file."""
    if not fragment_path or not os.path.isfile(fragment_path):
        return None
    try:
        with open(fragment_path, 'r', encoding='utf-8', errors='replace') as fh:
            contenido = fh.read()
    except OSError:
        return None
    for linea in contenido.splitlines():
        if not linea.strip().startswith('ExecStart'):
            continue
        encontrado = _RE_PUERTO.search(linea)
        if encontrado:
            return int(encontrado.group(1))
    return None


def estado_servicio(unidad, timeout=10):
    """Devuelve el estado de una unidad systemd.

    Nunca lanza excepción: si systemctl no está disponible lo indica en 'error'.
    """
    resultado = {
        'unidad': unidad,
        'existe': False,
        'activo': False,
        'estado': 'desconocido',
        'subestado': None,
        'habilitado': None,
        'pid': None,
        'memoria_bytes': None,
        'memoria': '-',
        'reinicios': None,
        'desde': None,
        'uptime_segundos': None,
        'uptime': '-',
        'puerto': None,
        'descripcion': None,
        'archivo': None,
        'creado': None,
        'error': None,
    }
    if not unidad:
        resultado['error'] = 'sin nombre de servicio'
        return resultado

    codigo, salida, error = ejecutar(
        ['systemctl', 'show', unidad, '--no-pager',
         '--property=' + ','.join(PROPIEDADES)],
        timeout=timeout,
    )
    if codigo == 127:
        resultado['error'] = 'systemctl no disponible en este servidor'
        return resultado
    if not salida:
        resultado['error'] = error or 'systemctl no devolvió información'
        return resultado

    datos = {}
    for linea in salida.splitlines():
        if '=' in linea:
            clave, _, valor = linea.partition('=')
            datos[clave.strip()] = valor.strip()

    load_state = datos.get('LoadState')
    resultado['existe'] = load_state not in (None, '', 'not-found', 'masked')
    resultado['estado'] = datos.get('ActiveState') or 'desconocido'
    resultado['subestado'] = datos.get('SubState') or None
    resultado['habilitado'] = datos.get('UnitFileState') or None
    resultado['descripcion'] = datos.get('Description') or None
    resultado['activo'] = resultado['estado'] in ('active', 'activating')

    if not resultado['existe']:
        resultado['estado'] = 'no-encontrado'
        resultado['error'] = 'La unidad %s no existe en systemd' % unidad
        return resultado

    pid = datos.get('MainPID')
    if pid and pid.isdigit() and int(pid) > 0:
        resultado['pid'] = int(pid)

    memoria = datos.get('MemoryCurrent')
    if memoria and memoria.isdigit():
        resultado['memoria_bytes'] = int(memoria)
        resultado['memoria'] = bytes_legible(int(memoria))

    reinicios = datos.get('NRestarts')
    if reinicios and reinicios.isdigit():
        resultado['reinicios'] = int(reinicios)

    inicio = (_parsear_fecha(datos.get('ExecMainStartTimestamp'))
              or _parsear_fecha(datos.get('ActiveEnterTimestamp'))
              or _parsear_fecha(datos.get('StateChangeTimestamp')))
    if inicio and resultado['activo']:
        resultado['desde'] = inicio.replace(microsecond=0).isoformat(sep=' ')
        segundos = int((datetime.datetime.now() - inicio).total_seconds())
        if segundos >= 0:
            resultado['uptime_segundos'] = segundos
            resultado['uptime'] = duracion_legible(segundos)

    fragmento = datos.get('FragmentPath')
    resultado['archivo'] = fragmento or None
    if fragmento and os.path.isfile(fragmento):
        try:
            marca = os.stat(fragmento).st_mtime
            resultado['creado'] = datetime.datetime.fromtimestamp(marca).strftime('%Y-%m-%d %H:%M')
        except OSError:
            pass
    resultado['puerto'] = _puerto_del_unit(fragmento)
    return resultado
