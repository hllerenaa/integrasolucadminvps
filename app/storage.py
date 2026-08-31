# -*- coding: utf-8 -*-
"""Tamaño en disco de la carpeta media de cada instancia (con caché en disco).

Calcular el tamaño de media es lo más costoso del panel, así que el resultado
se guarda en var/media_cache.json y sólo se recalcula cuando vence el TTL o
cuando se pide un refresco forzado.
"""
from __future__ import annotations

import json
import os
import threading
import time

from .utils import bytes_legible, ejecutar

_LOCK = threading.Lock()
_CACHE = None
_CACHE_PATH = None


def _ruta_cache(config):
    return os.path.join(config.var_dir, 'media_cache.json')


def _cargar_cache(config):
    global _CACHE, _CACHE_PATH
    if _CACHE is not None:
        return _CACHE
    _CACHE_PATH = _ruta_cache(config)
    try:
        with open(_CACHE_PATH, 'r', encoding='utf-8') as fh:
            _CACHE = json.load(fh)
    except Exception:
        _CACHE = {}
    return _CACHE


def _guardar_cache(config):
    if _CACHE is None:
        return
    ruta = _ruta_cache(config)
    tmp = ruta + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(_CACHE, fh)
        os.replace(tmp, ruta)
    except Exception:
        pass


def _du(ruta, timeout):
    """Tamaño en bytes usando du; si falla, recorre con os.walk."""
    codigo, salida, error = ejecutar(['du', '-sb', ruta], timeout=timeout)
    if codigo == 0 and salida:
        try:
            return int(salida.split()[0]), None
        except (ValueError, IndexError):
            pass
    if codigo == 124:
        return None, error
    total = 0
    try:
        for raiz, _dirs, archivos in os.walk(ruta, onerror=None):
            for archivo in archivos:
                try:
                    total += os.path.getsize(os.path.join(raiz, archivo))
                except OSError:
                    continue
        return total, None
    except Exception as ex:
        return None, error or str(ex)


def tamano_media(instancia, config, forzar=False):
    """Devuelve el tamaño de <instalación>/media, usando caché."""
    ruta = instancia.media_path
    resultado = {
        'ruta': ruta,
        'existe': os.path.isdir(ruta),
        'bytes': None,
        'tamano': '-',
        'calculado': None,
        'desde_cache': False,
        'error': None,
    }
    if not resultado['existe']:
        resultado['error'] = 'No existe la carpeta media'
        return resultado

    ttl = int(config.get('ttl_media') or 21600)
    ahora = time.time()

    with _LOCK:
        cache = _cargar_cache(config)
        entrada = cache.get(ruta)

    if entrada and not forzar and (ahora - entrada.get('ts', 0)) < ttl:
        resultado.update({
            'bytes': entrada.get('bytes'),
            'tamano': bytes_legible(entrada.get('bytes')),
            'calculado': entrada.get('fecha'),
            'desde_cache': True,
        })
        return resultado

    tamano, error = _du(ruta, int(config.get('timeout_du') or 120))
    if tamano is None:
        resultado['error'] = error or 'No se pudo calcular el tamaño'
        if entrada:  # se conserva el último valor conocido
            resultado.update({
                'bytes': entrada.get('bytes'),
                'tamano': bytes_legible(entrada.get('bytes')),
                'calculado': entrada.get('fecha'),
                'desde_cache': True,
            })
        return resultado

    fecha = time.strftime('%Y-%m-%d %H:%M:%S')
    with _LOCK:
        cache = _cargar_cache(config)
        cache[ruta] = {'bytes': tamano, 'ts': ahora, 'fecha': fecha}
        _guardar_cache(config)

    resultado.update({'bytes': tamano, 'tamano': bytes_legible(tamano), 'calculado': fecha})
    return resultado


def info_git(ruta, timeout=10):
    """Rama, commit y fecha del último commit de la instalación."""
    datos = {'rama': None, 'commit': None, 'fecha': None, 'error': None}
    if not os.path.isdir(os.path.join(ruta, '.git')):
        datos['error'] = 'sin repositorio git'
        return datos
    codigo, salida, error = ejecutar(
        ['git', '-C', ruta, 'log', '-1', '--format=%h|%cd|%s', '--date=format:%Y-%m-%d %H:%M'],
        timeout=timeout,
    )
    if codigo == 0 and salida:
        partes = salida.split('|', 2)
        datos['commit'] = partes[0]
        if len(partes) > 1:
            datos['fecha'] = partes[1]
        if len(partes) > 2:
            datos['mensaje'] = partes[2]
    else:
        datos['error'] = error or 'no se pudo leer el log de git'

    codigo, salida, _ = ejecutar(['git', '-C', ruta, 'rev-parse', '--abbrev-ref', 'HEAD'],
                                 timeout=timeout)
    if codigo == 0 and salida:
        datos['rama'] = salida
    return datos


def fecha_instalacion(ruta):
    """Fecha aproximada de creación de la carpeta de la instalación."""
    candidatos = []
    for objetivo in (ruta, os.path.join(ruta, 'credenciales.json'), os.path.join(ruta, 'manage.py')):
        try:
            st = os.stat(objetivo)
        except OSError:
            continue
        candidatos.append(min(st.st_ctime, st.st_mtime))
    if not candidatos:
        return None
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(min(candidatos)))


def uso_disco(ruta='/'):
    """Uso del sistema de archivos donde vive el servidor."""
    try:
        st = os.statvfs(ruta)
        total = st.f_blocks * st.f_frsize
        libre = st.f_bavail * st.f_frsize
        usado = total - (st.f_bfree * st.f_frsize)
        return {
            'total_bytes': total, 'total': bytes_legible(total),
            'usado_bytes': usado, 'usado': bytes_legible(usado),
            'libre_bytes': libre, 'libre': bytes_legible(libre),
            'porcentaje': round(usado * 100.0 / total, 1) if total else None,
        }
    except Exception as ex:
        return {'error': str(ex)}
