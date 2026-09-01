# -*- coding: utf-8 -*-
"""Backups de las bases de datos de cada instancia.

Un backup es un pg_dump comprimido guardado en la carpeta configurada
(por defecto /home/backups/<cliente>/). Se listan los que hay en disco, se
crean desde el panel y se aplica una retención por instancia.
"""
from __future__ import annotations

import datetime
import glob
import os
import re

from .credenciales import leer as leer_credenciales
from .utils import bytes_legible, ejecutar

# Acepta los nombres del panel (cliente_20260901_101500.sql.gz) y los de
# backupall.sh (basedatos_20260901.zip / .backup).
NOMBRE = re.compile(r'^(?P<clave>.+?)_(?P<fecha>\d{8}(?:_\d{6})?)\.'
                    r'(?:sql(?:\.gz)?|backup|zip|dump|tar(?:\.gz)?)$')
EXTENSIONES = ('*.sql', '*.sql.gz', '*.backup', '*.zip', '*.dump', '*.tar', '*.tar.gz')


def carpeta_base(config):
    cfg = config.get('backups') or {}
    return cfg.get('destino') or '/home/backups'


def repositorios(config):
    """Carpetas adicionales que sólo se leen (p.ej. la de backupall.sh)."""
    cfg = config.get('backups') or {}
    return [r for r in (cfg.get('repositorios') or []) if r]


def carpeta_de(config, cliente):
    return os.path.join(carpeta_base(config), cliente)


def _describir(ruta, cliente=None, solo_lectura=False):
    try:
        st = os.stat(ruta)
    except OSError:
        return None
    nombre = os.path.basename(ruta)
    encontrado = NOMBRE.match(nombre)
    fecha = None
    clave = None
    if encontrado:
        clave = encontrado.group('clave')
        cliente = cliente or clave
        texto = encontrado.group('fecha')
        for formato in ('%Y%m%d_%H%M%S', '%Y%m%d'):
            try:
                fecha = datetime.datetime.strptime(texto, formato)
                break
            except ValueError:
                continue
    if fecha is None:
        fecha = datetime.datetime.fromtimestamp(st.st_mtime)
    return {
        'archivo': ruta,
        'nombre': nombre,
        'cliente': cliente,
        'clave': clave or cliente,
        'solo_lectura': bool(solo_lectura),
        'bytes': st.st_size,
        'tamano': bytes_legible(st.st_size),
        'fecha': fecha.strftime('%Y-%m-%d %H:%M'),
        'dias': (datetime.datetime.now() - fecha).days,
        'comprimido': ruta.endswith('.gz'),
        'sospechoso': st.st_size < 51200,   # menos de 50 KB: casi seguro un dump fallido
    }


def _archivos_en(carpeta, con_subcarpetas=True, solo_lectura=False):
    encontrados = []
    for extension in EXTENSIONES:
        patrones = [os.path.join(carpeta, extension)]
        if con_subcarpetas:
            patrones.append(os.path.join(carpeta, '*', extension))
        for patron in patrones:
            for ruta in glob.glob(patron):
                if os.path.isdir(ruta):
                    continue
                cliente = None
                if os.path.dirname(ruta) != os.path.normpath(carpeta):
                    cliente = os.path.basename(os.path.dirname(ruta))
                datos = _describir(ruta, cliente, solo_lectura)
                if datos:
                    encontrados.append(datos)
    return encontrados


def listar(config, instancias=None, limite_por_cliente=25):
    """Backups en disco (carpeta del panel + repositorios), por instancia."""
    base = carpeta_base(config)
    archivos = _archivos_en(base)
    for repositorio in repositorios(config):
        archivos.extend(_archivos_en(repositorio, solo_lectura=True))

    por_cliente = {}
    for datos in archivos:
        por_cliente.setdefault(datos['cliente'] or 'sin-cliente', []).append(datos)

    for cliente in por_cliente:
        por_cliente[cliente].sort(key=lambda b: b['fecha'], reverse=True)

    filas = []
    vistos = set()
    for inst in (instancias or []):
        cliente = inst.get('cliente')
        base_datos = (inst.get('db') or {}).get('dbname')
        vistos.add(cliente)
        # Los backups pueden estar nombrados por cliente o por base de datos
        # (backupall.sh usa el nombre de la base).
        archivos = list(por_cliente.get(cliente, []))
        if base_datos and base_datos != cliente:
            vistos.add(base_datos)
            archivos += por_cliente.get(base_datos, [])
            archivos.sort(key=lambda b: b['fecha'], reverse=True)
        ultimo = archivos[0] if archivos else None
        filas.append({
            'id': inst.get('id'), 'cliente': cliente, 'tipo': inst.get('tipo'),
            'base': (inst.get('db') or {}).get('dbname'),
            'base_tamano': (inst.get('db') or {}).get('tamano'),
            'oculta': bool(inst.get('oculta')),
            'total': len(archivos),
            'ultimo': ultimo,
            'dias': ultimo['dias'] if ultimo else None,
            'ocupado': bytes_legible(sum(a['bytes'] for a in archivos)),
            'archivos': archivos[:limite_por_cliente],
        })
    # Carpetas con backups de instancias que ya no existen
    for cliente, archivos in por_cliente.items():
        if cliente in vistos:
            continue
        filas.append({
            'id': None, 'cliente': cliente, 'tipo': 'huérfano', 'base': None,
            'total': len(archivos), 'ultimo': archivos[0] if archivos else None,
            'dias': archivos[0]['dias'] if archivos else None,
            'ocupado': bytes_legible(sum(a['bytes'] for a in archivos)),
            'archivos': archivos[:limite_por_cliente],
        })

    filas.sort(key=lambda f: (f['dias'] is None, -(f['dias'] or 0)), reverse=False)
    total_bytes = sum(a['bytes'] for archivos in por_cliente.values() for a in archivos)
    return {
        'carpeta': base,
        'repositorios': repositorios(config),
        'instancias': filas,
        'total_archivos': sum(len(a) for a in por_cliente.values()),
        'total_bytes': total_bytes,
        'total_tamano': bytes_legible(total_bytes),
        'retencion': int((config.get('backups') or {}).get('retencion') or 7),
        'alerta_dias': int((config.get('backups') or {}).get('alerta_dias') or 3),
    }


def _credenciales(instancia_datos):
    datos = leer_credenciales(instancia_datos, con_secretos=True)
    if not datos.get('ok'):
        return None, datos.get('error')
    return datos['datos'], None


def crear(tarea, config, colector, ids=None, bases=None):
    """Genera el pg_dump comprimido de instancias y/o de bases sueltas."""
    cfg = config.get('backups') or {}
    retencion = int(cfg.get('retencion') or 7)
    comprimir = cfg.get('comprimir', True)

    instantanea = colector.snapshot(incluir_ocultas=True)
    objetivo = []
    if ids or not bases:
        objetivo = [i for i in instantanea['instancias'] if not ids or i.get('id') in ids]
    if not objetivo and not bases:
        tarea.log('No hay instancias que respaldar', 'error')
        tarea.estado = 'error'
        return

    resultados = []
    for inst in objetivo:
        cliente = inst.get('cliente')
        indice = tarea.paso('Backup de %s' % cliente)
        credenciales, error = _credenciales(inst)
        if not credenciales:
            tarea.paso_error(indice, 'Sin credenciales: %s' % error)
            resultados.append({'cliente': cliente, 'ok': False, 'error': error})
            continue

        base = credenciales.get('POSTGRES_DBNAME')
        if not base:
            tarea.paso_error(indice, 'credenciales.json sin POSTGRES_DBNAME')
            continue

        carpeta = carpeta_de(config, cliente)
        os.makedirs(carpeta, exist_ok=True)
        marca = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        destino = os.path.join(carpeta, '%s_%s.sql' % (cliente, marca))

        entorno = dict(os.environ)
        entorno['PGPASSWORD'] = credenciales.get('POSTGRES_PASSWORD') or ''
        comando = ['pg_dump',
                   '-h', credenciales.get('POSTGRES_HOST') or 'localhost',
                   '-U', credenciales.get('POSTGRES_USER') or 'postgres',
                   '-f', destino, base]
        codigo, _salida = tarea.ejecutar(comando, entorno=entorno, timeout=7200, critico=False,
                                         ocultar=[credenciales.get('POSTGRES_PASSWORD')])
        if codigo != 0 or not os.path.isfile(destino):
            tarea.paso_error(indice, 'pg_dump falló')
            resultados.append({'cliente': cliente, 'ok': False, 'error': 'pg_dump falló'})
            if os.path.isfile(destino):
                os.remove(destino)
            continue

        tamano = os.path.getsize(destino)
        if tamano < 51200:
            tarea.log('  El dump pesa sólo %s: se conserva, pero revísalo' % bytes_legible(tamano),
                      'aviso')

        if comprimir:
            codigo, _salida = tarea.ejecutar(['gzip', '-f', destino], timeout=3600, critico=False)
            if codigo == 0:
                destino += '.gz'
                tamano = os.path.getsize(destino)

        tarea.paso_ok(indice, '%s (%s)' % (os.path.basename(destino), bytes_legible(tamano)))
        resultados.append({'cliente': cliente, 'ok': True, 'archivo': destino,
                           'bytes': tamano, 'tamano': bytes_legible(tamano)})

        borrados = aplicar_retencion(config, cliente, retencion)
        if borrados:
            tarea.log('  Retención: se eliminaron %s backup(s) antiguo(s)' % len(borrados))

    # Bases sueltas (sin instancia): se usan las credenciales de una instancia
    # cualquiera del mismo servidor PostgreSQL.
    for base_suelta in (bases or []):
        indice = tarea.paso('Backup de la base %s' % base_suelta)
        credenciales = None
        for inst in instantanea['instancias']:
            posibles, _error = _credenciales(inst)
            if posibles and posibles.get('POSTGRES_HOST'):
                credenciales = posibles
                break
        if not credenciales:
            tarea.paso_error(indice, 'No hay credenciales de PostgreSQL disponibles')
            resultados.append({'cliente': base_suelta, 'ok': False, 'error': 'sin credenciales'})
            continue

        carpeta = carpeta_de(config, base_suelta)
        os.makedirs(carpeta, exist_ok=True)
        marca = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        destino = os.path.join(carpeta, '%s_%s.sql' % (base_suelta, marca))
        entorno = dict(os.environ)
        entorno['PGPASSWORD'] = credenciales.get('POSTGRES_PASSWORD') or ''
        codigo, _salida = tarea.ejecutar(
            ['pg_dump', '-h', credenciales.get('POSTGRES_HOST') or 'localhost',
             '-U', credenciales.get('POSTGRES_USER') or 'postgres', '-f', destino, base_suelta],
            entorno=entorno, timeout=7200, critico=False,
            ocultar=[credenciales.get('POSTGRES_PASSWORD')])
        if codigo != 0 or not os.path.isfile(destino):
            tarea.paso_error(indice, 'pg_dump falló')
            resultados.append({'cliente': base_suelta, 'ok': False, 'error': 'pg_dump falló'})
            continue
        if cfg.get('comprimir', True):
            if tarea.ejecutar(['gzip', '-f', destino], timeout=3600, critico=False)[0] == 0:
                destino += '.gz'
        tarea.paso_ok(indice, '%s (%s)' % (os.path.basename(destino),
                                           bytes_legible(os.path.getsize(destino))))
        resultados.append({'cliente': base_suelta, 'ok': True, 'archivo': destino})

    ok = sum(1 for r in resultados if r.get('ok'))
    tarea.log('Backups correctos: %s de %s' % (ok, len(resultados)),
              'ok' if ok == len(resultados) else 'aviso')
    tarea.datos['resultados'] = resultados
    if ok == 0:
        tarea.estado = 'error'


def aplicar_retencion(config, cliente, retencion):
    """Deja sólo los N backups más recientes de un cliente."""
    carpeta = carpeta_de(config, cliente)
    archivos = sorted(glob.glob(os.path.join(carpeta, '*.sql*')),
                      key=lambda r: os.path.getmtime(r), reverse=True)
    borrados = []
    for viejo in archivos[max(1, retencion):]:
        try:
            os.remove(viejo)
            borrados.append(viejo)
        except OSError:
            pass
    return borrados


def eliminar(config, archivo):
    """Borra un backup concreto, sólo dentro de la carpeta del panel.

    Los repositorios externos (como /home/db_repository) se pueden listar y
    descargar, pero no se tocan desde el panel.
    """
    base = os.path.realpath(carpeta_base(config))
    real = os.path.realpath(archivo)
    if not real.startswith(base + os.sep):
        return {'ok': False, 'error': 'El archivo no está dentro de %s' % base}
    if not os.path.isfile(real):
        return {'ok': False, 'error': 'No existe %s' % archivo}
    try:
        os.remove(real)
    except OSError as ex:
        return {'ok': False, 'error': str(ex)}
    return {'ok': True, 'archivo': real}


def ruta_valida(config, archivo):
    """True si el archivo está en la carpeta del panel o en un repositorio."""
    real = os.path.realpath(archivo)
    if not os.path.isfile(real):
        return False
    permitidas = [carpeta_base(config)] + repositorios(config)
    return any(real.startswith(os.path.realpath(c) + os.sep) for c in permitidas)
