# -*- coding: utf-8 -*-
"""Consulta del estado de los servicios systemd de cada instancia."""
from __future__ import annotations

import datetime
import os
import re
import threading
import time

from .utils import bytes_legible, duracion_legible, ejecutar

PROPIEDADES = (
    'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'MainPID',
    'MemoryCurrent', 'MemoryPeak', 'CPUUsageNSec', 'TasksCurrent', 'NRestarts',
    'ExecMainStartTimestamp', 'ActiveEnterTimestamp', 'StateChangeTimestamp',
    'FragmentPath', 'Description',
)

# Muestras anteriores de CPU por unidad, para calcular el porcentaje entre
# dos refrescos: {unidad: (nanosegundos_de_cpu, momento)}
_MUESTRAS_CPU = {}
_LOCK_CPU = threading.Lock()
VIGENCIA_MUESTRA = 900   # segundos: pasada esa edad se vuelve a muestrear al vuelo

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


def estado_socket(nombre, timeout=8):
    """Estado de la unidad <nombre>.socket (activación por socket)."""
    datos = {'unidad': '%s.socket' % nombre, 'existe': False, 'activo': False,
             'estado': None, 'escucha': None}
    codigo, salida, _ = ejecutar(
        ['systemctl', 'show', '%s.socket' % nombre, '--no-pager',
         '--property=LoadState,ActiveState,SubState,Listen'], timeout=timeout)
    if codigo != 0 or not salida:
        return datos
    valores = {}
    for linea in salida.splitlines():
        if '=' in linea:
            clave, _, valor = linea.partition('=')
            valores[clave.strip()] = valor.strip()
    datos['existe'] = valores.get('LoadState') not in (None, '', 'not-found', 'masked')
    datos['estado'] = valores.get('ActiveState')
    datos['activo'] = valores.get('ActiveState') in ('active', 'listening', 'activating')
    datos['escucha'] = valores.get('Listen') or None
    return datos


def recursos_del_sistema():
    """RAM total, RAM libre, núcleos y carga del servidor."""
    datos = {'ram_total': None, 'ram_disponible': None, 'nucleos': os.cpu_count(),
             'carga': None, 'carga_pct': None}
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as fh:
            for linea in fh:
                if linea.startswith('MemTotal:'):
                    datos['ram_total'] = int(linea.split()[1]) * 1024
                elif linea.startswith('MemAvailable:'):
                    datos['ram_disponible'] = int(linea.split()[1]) * 1024
                    break
    except OSError:
        pass
    try:
        with open('/proc/loadavg', 'r', encoding='utf-8') as fh:
            carga = float(fh.read().split()[0])
        datos['carga'] = carga
        if datos['nucleos']:
            datos['carga_pct'] = round(carga * 100.0 / datos['nucleos'], 1)
    except (OSError, ValueError):
        pass
    if datos['ram_total'] and datos['ram_disponible'] is not None:
        usada = datos['ram_total'] - datos['ram_disponible']
        datos['ram_usada'] = usada
        datos['ram_pct'] = round(usada * 100.0 / datos['ram_total'], 1)
        datos['ram_total_legible'] = bytes_legible(datos['ram_total'])
        datos['ram_usada_legible'] = bytes_legible(usada)
        datos['ram_disponible_legible'] = bytes_legible(datos['ram_disponible'])
    return datos


def _cpu_nsec(unidad, timeout=10):
    codigo, salida, _ = ejecutar(
        ['systemctl', 'show', unidad, '--no-pager', '--property=CPUUsageNSec'], timeout=timeout)
    if codigo != 0 or '=' not in (salida or ''):
        return None
    valor = salida.split('=', 1)[1].strip()
    return int(valor) if valor.isdigit() else None


def _porcentaje_cpu(unidad, nsec, timeout=10):
    """% de un núcleo que consume la unidad, comparando dos muestras.

    Si no hay muestra previa reciente se toma una segunda lectura al vuelo,
    para no dejar la columna vacía en el primer refresco.
    """
    if nsec is None:
        return None
    ahora = time.time()
    with _LOCK_CPU:
        anterior = _MUESTRAS_CPU.get(unidad)
        _MUESTRAS_CPU[unidad] = (nsec, ahora)

    if not anterior or (ahora - anterior[1]) > VIGENCIA_MUESTRA:
        time.sleep(0.7)
        nuevo = _cpu_nsec(unidad, timeout=timeout)
        fin = time.time()
        with _LOCK_CPU:
            _MUESTRAS_CPU[unidad] = (nuevo if nuevo is not None else nsec, fin)
        if nuevo is None or fin <= ahora:
            return None
        anterior, nsec, ahora = (nsec, ahora), nuevo, fin

    delta_ns = nsec - anterior[0]
    delta_s = ahora - anterior[1]
    if delta_ns < 0 or delta_s <= 0:
        return None
    return round(delta_ns / (delta_s * 1e9) * 100.0, 1)


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
        'memoria_pico': None,
        'cpu_pct': None,
        'cpu_segundos': None,
        'tareas': None,
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

    pico = datos.get('MemoryPeak')
    if pico and pico.isdigit():
        resultado['memoria_pico'] = bytes_legible(int(pico))

    tareas = datos.get('TasksCurrent')
    if tareas and tareas.isdigit():
        resultado['tareas'] = int(tareas)

    cpu_nsec = datos.get('CPUUsageNSec')
    cpu_nsec = int(cpu_nsec) if (cpu_nsec or '').isdigit() else None
    resultado['cpu_segundos'] = round(cpu_nsec / 1e9, 1) if cpu_nsec else None
    if resultado['activo']:
        resultado['cpu_pct'] = _porcentaje_cpu(unidad, cpu_nsec, timeout=timeout)

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
