# -*- coding: utf-8 -*-
"""Lectura y edición del credenciales.json de cada instancia.

Las claves (contraseñas, tokens) viajan enmascaradas salvo que se pidan
explícitamente, y toda escritura deja un respaldo con fecha.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import time

# Cualquier clave que contenga uno de estos fragmentos se considera secreta.
FRAGMENTOS_SECRETOS = ('PASSWORD', 'PASS', 'SECRET', 'TOKEN', 'KEY', 'CLAVE', 'API')
RESPALDOS_A_CONSERVAR = 8


def es_secreta(clave):
    clave = (clave or '').upper()
    return any(f in clave for f in FRAGMENTOS_SECRETOS)


def enmascarar(datos):
    """Devuelve una copia con los valores secretos ocultos."""
    salida = {}
    for clave, valor in (datos or {}).items():
        if es_secreta(clave) and isinstance(valor, str) and valor:
            salida[clave] = '••••••••' + (' (%s caracteres)' % len(valor))
        else:
            salida[clave] = valor
    return salida


def ruta_archivo(instancia_datos):
    return os.path.join(instancia_datos.get('ruta') or '', 'credenciales.json')


def leer(instancia_datos, con_secretos=False):
    """Lee el credenciales.json de una instancia."""
    archivo = ruta_archivo(instancia_datos)
    if not os.path.isfile(archivo):
        return {'ok': False, 'error': 'No existe %s' % archivo, 'archivo': archivo}
    try:
        with open(archivo, 'r', encoding='utf-8') as fh:
            crudo = fh.read()
        datos = json.loads(crudo)
    except json.JSONDecodeError as ex:
        return {'ok': False, 'archivo': archivo, 'error': 'JSON inválido: %s' % ex,
                'contenido_crudo': crudo}
    except OSError as ex:
        return {'ok': False, 'archivo': archivo, 'error': str(ex)}

    try:
        st = os.stat(archivo)
        modificado = time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))
        permisos = oct(st.st_mode & 0o777)[2:]
    except OSError:
        modificado, permisos = None, None

    visibles = datos if con_secretos else enmascarar(datos)
    return {
        'ok': True,
        'archivo': archivo,
        'modificado': modificado,
        'permisos': permisos,
        'con_secretos': bool(con_secretos),
        'claves_secretas': sorted([k for k in datos if es_secreta(k)]),
        'datos': visibles,
        'texto': json.dumps(visibles, indent=2, ensure_ascii=False),
        'respaldos': respaldos(archivo),
    }


def respaldos(archivo):
    return sorted(
        [{'archivo': r, 'fecha': time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(r)))}
         for r in glob.glob(archivo + '.bak-*') if os.path.isfile(r)],
        key=lambda x: x['archivo'], reverse=True)


def _limpiar_respaldos(archivo):
    antiguos = sorted(glob.glob(archivo + '.bak-*'), reverse=True)[RESPALDOS_A_CONSERVAR:]
    for viejo in antiguos:
        try:
            os.remove(viejo)
        except OSError:
            pass


def guardar(instancia_datos, texto):
    """Valida y guarda el credenciales.json dejando un respaldo previo."""
    archivo = ruta_archivo(instancia_datos)
    if not os.path.isfile(archivo):
        return {'ok': False, 'error': 'No existe %s' % archivo}

    try:
        nuevos = json.loads(texto)
    except (json.JSONDecodeError, TypeError) as ex:
        return {'ok': False, 'error': 'El contenido no es un JSON válido: %s' % ex}
    if not isinstance(nuevos, dict):
        return {'ok': False, 'error': 'El archivo debe ser un objeto JSON'}

    try:
        with open(archivo, 'r', encoding='utf-8') as fh:
            actuales = json.load(fh)
    except Exception:
        actuales = {}

    # Un valor enmascarado significa "no lo cambies": se restaura el original.
    restauradas = []
    for clave, valor in list(nuevos.items()):
        if isinstance(valor, str) and valor.startswith('••••') and clave in actuales:
            nuevos[clave] = actuales[clave]
            restauradas.append(clave)

    faltantes = [c for c in ('POSTGRES_DBNAME', 'POSTGRES_HOST', 'POSTGRES_USER')
                 if c in actuales and c not in nuevos]
    if faltantes:
        return {'ok': False,
                'error': 'Faltan claves obligatorias que sí estaban antes: %s' % ', '.join(faltantes)}

    respaldo = '%s.bak-%s' % (archivo, time.strftime('%Y%m%d_%H%M%S'))
    try:
        shutil.copy2(archivo, respaldo)
    except OSError as ex:
        return {'ok': False, 'error': 'No se pudo crear el respaldo: %s' % ex}

    try:
        modo = os.stat(archivo).st_mode & 0o777
        temporal = archivo + '.tmp'
        with open(temporal, 'w', encoding='utf-8') as fh:
            json.dump(nuevos, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
        os.chmod(temporal, modo)
        os.replace(temporal, archivo)
    except OSError as ex:
        return {'ok': False, 'error': 'No se pudo escribir el archivo: %s' % ex, 'respaldo': respaldo}

    _limpiar_respaldos(archivo)

    cambiadas = sorted([c for c in nuevos
                        if c not in actuales or nuevos[c] != actuales.get(c)])
    return {
        'ok': True,
        'archivo': archivo,
        'respaldo': respaldo,
        'claves_modificadas': cambiadas,
        'claves_conservadas': restauradas,
        'aviso': 'Reinicia el servicio de la instancia para que tome los cambios',
    }
