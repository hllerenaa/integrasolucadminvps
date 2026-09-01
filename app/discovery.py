# -*- coding: utf-8 -*-
"""Descubrimiento de instancias pryinventario / pryrestaurante en el servidor.

Recorre los directorios base (por defecto /home) buscando carpetas de proyecto
(pryinventario, pryrestaurante) igual que hacen los scripts allupdateweb, y lee
el credenciales.json de cada instalación para saber a qué base de datos apunta.
"""
from __future__ import annotations

import json
import os

CLAVES_CREDENCIALES_PUBLICAS = (
    'POSTGRES_USER', 'POSTGRES_HOST', 'POSTGRES_DBNAME', 'DOMINIO_GENERAL',
    'DOMINIO_TIENDA', 'DEBUG', 'USE_SSL', 'ES_TIENDA', 'EMAIL_HOST_USER',
)


class Instancia(object):
    """Una instalación concreta de un sistema en el servidor."""

    def __init__(self, cliente, tipo, ruta, servicio, credenciales, error_credenciales=None):
        self.cliente = cliente
        self.tipo = tipo                      # 'inventario' | 'restaurante'
        self.ruta = ruta
        self.servicio = servicio
        self.credenciales = credenciales or {}
        self.error_credenciales = error_credenciales

    @property
    def id(self):
        return '%s|%s' % (self.cliente, self.tipo)

    @property
    def media_path(self):
        return os.path.join(self.ruta, 'media')

    @property
    def dominio(self):
        return (self.credenciales.get('DOMINIO_GENERAL') or '').strip()

    @property
    def url(self):
        dominio = self.dominio
        if not dominio:
            return None
        esquema = 'https' if self.credenciales.get('USE_SSL') else 'http'
        return '%s://%s' % (esquema, dominio)

    def credenciales_publicas(self):
        """Credenciales sin la contraseña de base de datos ni la de correo."""
        return {k: self.credenciales.get(k) for k in CLAVES_CREDENCIALES_PUBLICAS
                if k in self.credenciales}

    def base_datos(self):
        return {
            'host': self.credenciales.get('POSTGRES_HOST'),
            'dbname': self.credenciales.get('POSTGRES_DBNAME'),
            'user': self.credenciales.get('POSTGRES_USER'),
            'password': self.credenciales.get('POSTGRES_PASSWORD'),
            'port': self.credenciales.get('POSTGRES_PORT') or 5432,
        }

    def as_dict(self):
        return {
            'id': self.id,
            'cliente': self.cliente,
            'tipo': self.tipo,
            'ruta': self.ruta,
            'servicio': self.servicio,
            'dominio': self.dominio,
            'url': self.url,
            'credenciales': self.credenciales_publicas(),
            'error_credenciales': self.error_credenciales,
        }


def _leer_credenciales(ruta):
    """Devuelve (dict, error) leyendo credenciales.json de la instalación."""
    archivo = os.path.join(ruta, 'credenciales.json')
    if not os.path.isfile(archivo):
        return {}, 'No existe credenciales.json'
    try:
        with open(archivo, 'r', encoding='utf-8') as fh:
            return json.load(fh), None
    except Exception as ex:
        return {}, 'credenciales.json ilegible: %s' % ex


def _candidatos(base_dirs, profundidad, nombres):
    """Genera rutas de proyecto encontradas hasta 'profundidad' niveles."""
    encontrados = []
    for base in base_dirs:
        if not os.path.isdir(base):
            continue
        # Nivel 1: /home/pryinventario  (poco común, pero se soporta)
        for tipo, nombre in nombres.items():
            ruta = os.path.join(base, nombre)
            if os.path.isdir(ruta):
                encontrados.append((os.path.basename(base.rstrip('/')), tipo, ruta))
        if profundidad < 2:
            continue
        # Nivel 2: /home/<cliente>/pryinventario
        try:
            entradas = sorted(os.scandir(base), key=lambda e: e.name)
        except OSError:
            continue
        for entrada in entradas:
            try:
                if not entrada.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            for tipo, nombre in nombres.items():
                ruta = os.path.join(entrada.path, nombre)
                if os.path.isdir(ruta):
                    encontrados.append((entrada.name, tipo, ruta))
    return encontrados


def descubrir(config):
    """Devuelve la lista de Instancia detectadas en el servidor."""
    nombres = config.get('proyectos') or {}
    # Nota: excluidos.txt se aplica en el colector, que ya conoce el nombre
    # real del servicio systemd de cada instancia.
    excluidos = {c.lower() for c in (config.get('excluir_clientes') or [])}
    instancias = []
    vistos = set()

    for cliente, tipo, ruta in _candidatos(config.get('base_dirs') or ['/home'],
                                           int(config.get('profundidad') or 2),
                                           nombres):
        if cliente.lower() in excluidos:
            continue
        override = config.override(cliente)
        if override.get('ignorar'):
            continue
        if ruta in vistos:
            continue
        vistos.add(ruta)

        credenciales, error = _leer_credenciales(ruta)
        if override.get('dominio'):
            credenciales = dict(credenciales)
            credenciales['DOMINIO_GENERAL'] = override['dominio']
        instancias.append(Instancia(
            cliente=cliente,
            tipo=tipo,
            ruta=ruta,
            servicio=config.nombre_servicio(cliente, tipo),
            credenciales=credenciales,
            error_credenciales=error,
        ))

    instancias.sort(key=lambda i: (i.cliente, i.tipo))
    return instancias
