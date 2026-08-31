# -*- coding: utf-8 -*-
"""Mapeo de unidades systemd -> carpeta de instalación.

El nombre del servicio no siempre coincide con el nombre del cliente, así que
en vez de adivinarlo se leen los archivos .service y se busca cuál apunta
realmente a /home/<cliente>/pryinventario (o pryrestaurante).
"""
from __future__ import annotations

import glob
import os
import re

DIRECTORIOS_UNIDADES = (
    '/etc/systemd/system',
    '/lib/systemd/system',
    '/usr/lib/systemd/system',
)

_RE_WORKDIR = re.compile(r'^\s*WorkingDirectory\s*=\s*(.+)$', re.I | re.M)
_RE_EXEC = re.compile(r'^\s*ExecStart\s*=\s*(.+)$', re.I | re.M)
_RE_PUERTO = re.compile(r'(?:--bind|-b)[= ]+(?:[\w\.\*]*:)?(\d{2,5})|runserver\s+(?:[\w\.\*]*:)?(\d{2,5})')


def _puerto(texto):
    encontrado = _RE_PUERTO.search(texto or '')
    if not encontrado:
        return None
    valor = encontrado.group(1) or encontrado.group(2)
    return int(valor) if valor else None


def cargar_unidades(config=None):
    """Lee los .service del servidor y devuelve una lista de descriptores."""
    directorios = DIRECTORIOS_UNIDADES
    if config and config.get('unidades_dirs'):
        directorios = tuple(config['unidades_dirs'])

    unidades = []
    vistos = set()
    for carpeta in directorios:
        if not os.path.isdir(carpeta):
            continue
        for archivo in sorted(glob.glob(os.path.join(carpeta, '*.service'))):
            nombre = os.path.basename(archivo)
            if nombre in vistos:
                continue
            vistos.add(nombre)
            try:
                with open(archivo, 'r', encoding='utf-8', errors='replace') as fh:
                    contenido = fh.read()
            except OSError:
                continue
            workdir = _RE_WORKDIR.search(contenido)
            ejecutar = _RE_EXEC.search(contenido)
            unidades.append({
                'unidad': nombre[:-8],           # sin .service
                'archivo': archivo,
                'workingdirectory': workdir.group(1).strip() if workdir else None,
                'execstart': ejecutar.group(1).strip() if ejecutar else None,
                'puerto': _puerto(ejecutar.group(1) if ejecutar else ''),
            })
    return unidades


def buscar_unidad(instancia, unidades, patron_servicio=None):
    """Encuentra la unidad systemd que corresponde a una instalación.

    1. WorkingDirectory igual a la ruta de la instancia (coincidencia exacta).
    2. La ruta aparece en ExecStart.
    3. El nombre esperado por convención ({cliente}).
    """
    ruta = os.path.normpath(instancia.ruta)
    for unidad in unidades:
        workdir = unidad.get('workingdirectory')
        if workdir and os.path.normpath(workdir) == ruta:
            return dict(unidad, origen='workingdirectory')
    for unidad in unidades:
        if unidad.get('execstart') and ruta in unidad['execstart']:
            return dict(unidad, origen='execstart')
    if patron_servicio:
        for unidad in unidades:
            if unidad['unidad'] == patron_servicio:
                return dict(unidad, origen='nombre')
    return None
