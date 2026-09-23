# -*- coding: utf-8 -*-
"""Lista de sistemas que no se muestran en el panel.

Se lee de excluidos.txt en la raíz del proyecto: una sola línea con los
nombres separados por comas (también se aceptan varias líneas y comentarios
con #). Sirve tanto el nombre del cliente/carpeta como el del servicio systemd.

    onepc,elgringo,demo

El archivo se relee en cada refresco: editarlo no obliga a reiniciar el panel.
"""
from __future__ import annotations

import os

NOMBRE_ARCHIVO = 'excluidos.txt'


def ruta(config):
    cfg = config.get('excluidos_archivo')
    if cfg:
        return cfg
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, NOMBRE_ARCHIVO)


def cargar(config):
    """Devuelve el conjunto de nombres excluidos (en minúsculas)."""
    archivo = ruta(config)
    if not os.path.isfile(archivo):
        return set()
    try:
        with open(archivo, 'r', encoding='utf-8', errors='replace') as fh:
            contenido = fh.read()
    except OSError:
        return set()

    nombres = set()
    for linea in contenido.splitlines():
        linea = linea.split('#', 1)[0]
        for parte in linea.replace(';', ',').split(','):
            nombre = parte.strip().lower()
            if nombre:
                nombres.add(nombre)
    return nombres


def guardar(config, nombres):
    """Escribe excluidos.txt con los nombres en una sola línea."""
    limpios, vistos = [], set()
    for nombre in nombres or []:
        nombre = (nombre or '').strip().lower()
        if nombre and nombre not in vistos:
            vistos.add(nombre)
            limpios.append(nombre)
    archivo = ruta(config)
    with open(archivo, 'w', encoding='utf-8') as fh:
        fh.write(','.join(limpios) + '\n')
    return {'archivo': archivo, 'nombres': limpios}


def desde_texto(texto):
    """Convierte lo que el usuario escribió (comas o líneas) en una lista."""
    nombres = []
    for linea in (texto or '').splitlines():
        linea = linea.split('#', 1)[0]
        for parte in linea.replace(';', ',').split(','):
            nombre = parte.strip().lower()
            if nombre:
                nombres.append(nombre)
    return nombres


def excluida(nombres, cliente=None, servicio=None):
    """True si el cliente o su servicio están en la lista."""
    if not nombres:
        return False
    return ((cliente or '').lower() in nombres) or ((servicio or '').lower() in nombres)
