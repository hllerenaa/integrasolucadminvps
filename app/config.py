# -*- coding: utf-8 -*-
"""Carga y validación de la configuración del panel."""
from __future__ import annotations

import hashlib
import json
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get('ADMINVPS_CONFIG', os.path.join(BASE_DIR, 'config.json'))
EJEMPLO_PATH = os.path.join(BASE_DIR, 'config.example.json')
VAR_DIR = os.environ.get('ADMINVPS_VAR', os.path.join(BASE_DIR, 'var'))

DEFAULTS = {
    'host': '0.0.0.0',
    'port': 8600,
    'titulo': 'Panel VPS - Integrasoluc',
    'base_dirs': ['/home'],
    'profundidad': 2,
    'proyectos': {'inventario': 'pryinventario', 'restaurante': 'pryrestaurante'},
    'excluir_clientes': [],
    'servicio_patron': '{cliente}',
    'overrides': {},
    'consultar_bd': True,
    'medir_media': True,
    'verificar_url': True,
    'intervalo_refresco': 300,
    'ttl_datos': 300,
    'ttl_media': 21600,
    'workers': 8,
    'db_connect_timeout': 6,
    'db_statement_timeout': 15000,
    'timeout_systemctl': 10,
    'timeout_du': 120,
    'timeout_url': 6,
    'timeout_accion': 60,
    'acciones': {'enabled': True, 'servicios': True, 'apache': True},
    'auth': {
        'enabled': True,
        'username': 'admin',
        'password_hash': '',
        'password': '',
        'api_token': '',
    },
    'secret_key': '',
}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def _mezclar(base, extra):
    """Mezcla recursiva de diccionarios (extra manda)."""
    salida = dict(base)
    for clave, valor in (extra or {}).items():
        if isinstance(valor, dict) and isinstance(salida.get(clave), dict):
            salida[clave] = _mezclar(salida[clave], valor)
        else:
            salida[clave] = valor
    return salida


class Config(dict):
    """Configuración del panel; se comporta como un dict."""

    path = CONFIG_PATH

    @property
    def var_dir(self):
        os.makedirs(VAR_DIR, exist_ok=True)
        return VAR_DIR

    def override(self, cliente):
        return (self.get('overrides') or {}).get(cliente, {}) or {}

    def nombre_servicio(self, cliente, tipo):
        ov = self.override(cliente)
        if ov.get('servicio'):
            return ov['servicio']
        patron = self.get('servicio_patron') or '{cliente}'
        return patron.format(cliente=cliente, tipo=tipo)

    def verificar_password(self, password):
        auth = self.get('auth') or {}
        esperado_hash = (auth.get('password_hash') or '').strip().lower()
        if esperado_hash:
            return secrets.compare_digest(hash_password(password), esperado_hash)
        esperado = auth.get('password') or ''
        if esperado:
            return secrets.compare_digest(password, esperado)
        return False


def cargar(path=None):
    """Carga config.json (o el ejemplo si aún no existe) y aplica valores por defecto."""
    path = path or CONFIG_PATH
    datos = {}
    origen = path
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8') as fh:
            datos = json.load(fh)
    elif os.path.isfile(EJEMPLO_PATH):
        origen = EJEMPLO_PATH
        with open(EJEMPLO_PATH, 'r', encoding='utf-8') as fh:
            datos = json.load(fh)

    cfg = Config(_mezclar(DEFAULTS, datos))
    cfg.path = origen

    # Overrides por variables de entorno (útiles en systemd / docker).
    if os.environ.get('ADMINVPS_PORT'):
        cfg['port'] = int(os.environ['ADMINVPS_PORT'])
    if os.environ.get('ADMINVPS_HOST'):
        cfg['host'] = os.environ['ADMINVPS_HOST']

    # Se ignoran claves de ejemplo dentro de "overrides".
    cfg['overrides'] = {
        k: v for k, v in (cfg.get('overrides') or {}).items() if not k.startswith('_')
    }

    if not cfg.get('secret_key'):
        cfg['secret_key'] = _secret_persistente(cfg)

    return cfg


def _secret_persistente(cfg):
    """Genera y guarda una secret key para las cookies de sesión."""
    ruta = os.path.join(cfg.var_dir, 'secret.key')
    if os.path.isfile(ruta):
        with open(ruta, 'r', encoding='utf-8') as fh:
            valor = fh.read().strip()
            if valor:
                return valor
    valor = secrets.token_hex(32)
    with open(ruta, 'w', encoding='utf-8') as fh:
        fh.write(valor)
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass
    return valor
