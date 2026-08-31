# -*- coding: utf-8 -*-
"""Archivos de log asociados a cada instancia y cuánto ocupan.

Se juntan tres orígenes:
  - los .log dentro de la carpeta del proyecto (y subcarpetas logs/)
  - los ErrorLog / CustomLog del vhost de Apache
  - los --access-logfile / --error-logfile del gunicorn en el .service
Se incluyen las rotaciones (archivo.1, archivo.2.gz, …).
"""
from __future__ import annotations

import glob
import os
import re
import time

from .utils import bytes_legible

_RE_GUNICORN_LOG = re.compile(r'--(?:access|error)-logfile[= ]+(\S+)')

PATRONES_PROYECTO = ('*.log', 'logs/*.log', 'logs/*', 'log/*.log', 'media/logs/*')


def _rotaciones(ruta):
    """archivo.log -> [archivo.log, archivo.log.1, archivo.log.2.gz, ...]"""
    encontrados = [ruta] + glob.glob(ruta + '.*')
    return [r for r in encontrados if os.path.isfile(r)]


def _describir(ruta, origen):
    try:
        st = os.stat(ruta)
    except OSError:
        return None
    return {
        'archivo': ruta,
        'origen': origen,
        'bytes': st.st_size,
        'tamano': bytes_legible(st.st_size),
        'modificado': time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime)),
        'rotado': bool(re.search(r'\.(\d+)(\.gz)?$', ruta)),
    }


def logs_de_instancia(instancia, vhost=None, unidad=None, limite=60):
    """Lista los archivos de log de una instancia con su tamaño."""
    resultado = {'archivos': [], 'bytes': 0, 'tamano': '0 B', 'total_archivos': 0,
                 'por_origen': {}, 'error': None}
    vistos = set()
    candidatos = []

    ruta = instancia.ruta if hasattr(instancia, 'ruta') else (instancia or {}).get('ruta')
    if ruta:
        for patron in PATRONES_PROYECTO:
            for archivo in glob.glob(os.path.join(ruta, patron)):
                if os.path.isfile(archivo):
                    candidatos.append((archivo, 'proyecto'))

    for clave in ('errorlog', 'customlog'):
        for destino in (vhost or {}).get(clave) or []:
            for archivo in _rotaciones(destino):
                candidatos.append((archivo, 'apache'))

    ejecutar = (unidad or {}).get('execstart') or ''
    for destino in _RE_GUNICORN_LOG.findall(ejecutar):
        if destino == '-':
            continue
        for archivo in _rotaciones(destino):
            candidatos.append((archivo, 'servicio'))

    for archivo, origen in candidatos:
        real = os.path.normpath(archivo)
        if real in vistos:
            continue
        vistos.add(real)
        datos = _describir(real, origen)
        if not datos:
            continue
        resultado['archivos'].append(datos)
        resultado['bytes'] += datos['bytes']
        resultado['por_origen'][origen] = resultado['por_origen'].get(origen, 0) + datos['bytes']

    resultado['archivos'].sort(key=lambda a: a['bytes'], reverse=True)
    resultado['total_archivos'] = len(resultado['archivos'])
    if len(resultado['archivos']) > limite:
        resultado['archivos'] = resultado['archivos'][:limite]
    resultado['tamano'] = bytes_legible(resultado['bytes'])
    resultado['por_origen_legible'] = {k: bytes_legible(v)
                                       for k, v in resultado['por_origen'].items()}
    if not resultado['total_archivos']:
        resultado['error'] = 'No se encontraron archivos de log para esta instancia'
    return resultado
