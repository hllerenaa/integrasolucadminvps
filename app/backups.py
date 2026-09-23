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


MARCA_FIN = b'PostgreSQL database dump complete'


def _cola(ruta, bytes_finales=8192):
    """Últimos bytes del dump, descomprimiendo si hace falta.

    Recorrer el .gz entero también valida su CRC: un archivo cortado o
    dañado lanza excepción antes de llegar al final.
    """
    if ruta.endswith('.gz'):
        import gzip
        cola = b''
        with gzip.open(ruta, 'rb') as fh:
            while True:
                bloque = fh.read(1024 * 1024)
                if not bloque:
                    break
                cola = (cola + bloque)[-bytes_finales:]
        return cola
    with open(ruta, 'rb') as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - bytes_finales))
        return fh.read()


def comprobar(ruta):
    """Revisa que un backup esté completo y se pueda leer. Devuelve (ok, mensaje)."""
    if not os.path.isfile(ruta):
        return False, 'No existe %s' % ruta
    nombre = os.path.basename(ruta)
    try:
        if nombre.endswith('.sql') or nombre.endswith('.sql.gz'):
            if MARCA_FIN in _cola(ruta):
                return True, 'Dump completo: termina con «%s»' % MARCA_FIN.decode()
            return False, ('El dump no termina con «%s»: pg_dump se cortó o el archivo está '
                           'incompleto' % MARCA_FIN.decode())
        if nombre.endswith('.zip'):
            import zipfile
            with zipfile.ZipFile(ruta) as zf:
                malo = zf.testzip()
                contenido = zf.namelist()
            if malo:
                return False, 'El zip está dañado (%s)' % malo
            return True, 'Zip íntegro: %s' % ', '.join(contenido[:5])
        if nombre.endswith('.backup') or nombre.endswith('.dump'):
            codigo, salida, error = ejecutar(['pg_restore', '--list', ruta], timeout=600)
            if codigo != 0:
                return False, 'pg_restore no puede leerlo: %s' % (error or salida)[:300]
            objetos = len([l for l in salida.splitlines() if l and not l.startswith(';')])
            return True, 'Formato custom legible: %s objetos en el índice' % objetos
    except Exception as ex:
        return False, 'No se pudo leer: %s' % ex
    return None, 'Formato sin verificación automática'


def _tamano_base(credenciales, base):
    """Tamaño de la base en bytes (None si no se pudo consultar)."""
    entorno = dict(os.environ)
    entorno['PGPASSWORD'] = credenciales.get('POSTGRES_PASSWORD') or ''
    import subprocess
    try:
        proc = subprocess.run(
            ['psql', '-h', credenciales.get('POSTGRES_HOST') or 'localhost',
             '-U', credenciales.get('POSTGRES_USER') or 'postgres', '-d', base,
             '-tAc', 'SELECT pg_database_size(current_database())'],
            env=entorno, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        texto = proc.stdout.decode('utf-8', 'replace').strip()
        return int(texto) if proc.returncode == 0 and texto.isdigit() else None
    except Exception:
        return None


def _volcar(tarea, config, credenciales, base, clave, indice):
    """pg_dump de una base con chequeo de espacio, verificación y compresión.

    Devuelve el dict de resultado; el paso `indice` queda marcado ok/error.
    """
    cfg = config.get('backups') or {}
    carpeta = carpeta_de(config, clave)
    os.makedirs(carpeta, exist_ok=True)

    # 1. Espacio: un dump SQL ocupa como mucho lo que la base (sin índices
    #    suele ser bastante menos). Sin espacio el dump queda cortado.
    tamano_bd = _tamano_base(credenciales, base)
    try:
        st = os.statvfs(carpeta)
        libre = st.f_bavail * st.f_frsize
    except OSError:
        libre = None
    if tamano_bd and libre is not None:
        tarea.log('  Base: %s · libre en %s: %s' % (bytes_legible(tamano_bd), carpeta,
                                                   bytes_legible(libre)))
        if libre < tamano_bd:
            tarea.paso_error(indice, 'No hay espacio: la base ocupa %s y quedan %s libres'
                             % (bytes_legible(tamano_bd), bytes_legible(libre)))
            return {'cliente': clave, 'base': base, 'ok': False, 'error': 'sin espacio en disco'}
        if libre < tamano_bd * 2:
            tarea.log('  Queda poco espacio en disco para backups', 'aviso')

    marca = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    destino = os.path.join(carpeta, '%s_%s.sql' % (clave, marca))
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
        if os.path.isfile(destino):
            os.remove(destino)
        return {'cliente': clave, 'base': base, 'ok': False, 'error': 'pg_dump falló'}

    # 2. Verificación: el dump debe terminar con la marca de pg_dump.
    ok, mensaje = comprobar(destino)
    if not ok:
        tarea.paso_error(indice, mensaje)
        os.remove(destino)
        return {'cliente': clave, 'base': base, 'ok': False, 'error': mensaje}
    tarea.log('  ✔ %s' % mensaje, 'ok')

    tamano = os.path.getsize(destino)
    if tamano < 51200:
        tarea.log('  El dump pesa sólo %s: se conserva, pero revísalo' % bytes_legible(tamano),
                  'aviso')

    if cfg.get('comprimir', True):
        codigo, _salida = tarea.ejecutar(['gzip', '-f', destino], timeout=3600, critico=False)
        if codigo == 0:
            destino += '.gz'
            codigo, _salida = tarea.ejecutar(['gzip', '-t', destino], timeout=3600, critico=False)
            if codigo != 0:
                tarea.paso_error(indice, 'El .gz no pasó la prueba de integridad (gzip -t)')
                return {'cliente': clave, 'base': base, 'ok': False, 'archivo': destino,
                        'error': 'gzip dañado'}
            tamano = os.path.getsize(destino)

    tarea.paso_ok(indice, '%s (%s) verificado' % (os.path.basename(destino), bytes_legible(tamano)))
    borrados = aplicar_retencion(config, clave, int(cfg.get('retencion') or 7))
    if borrados:
        tarea.log('  Retención: se eliminaron %s backup(s) antiguo(s)' % len(borrados))
    return {'cliente': clave, 'base': base, 'ok': True, 'archivo': destino,
            'nombre': os.path.basename(destino), 'bytes': tamano,
            'tamano': bytes_legible(tamano)}


def crear(tarea, config, colector, ids=None, bases=None):
    """Genera el pg_dump comprimido y verificado de instancias y/o bases sueltas."""
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
            resultados.append({'cliente': cliente, 'ok': False, 'error': 'sin POSTGRES_DBNAME'})
            continue
        resultados.append(_volcar(tarea, config, credenciales, base, cliente, indice))

    # Bases sueltas (sin instancia): se usan las credenciales de una instancia
    # cualquiera del mismo servidor PostgreSQL.
    credenciales_servidor = None
    for base_suelta in (bases or []):
        indice = tarea.paso('Backup de la base %s' % base_suelta)
        if credenciales_servidor is None:
            for inst in instantanea['instancias']:
                posibles, _error = _credenciales(inst)
                if posibles and posibles.get('POSTGRES_HOST'):
                    credenciales_servidor = posibles
                    break
        if not credenciales_servidor:
            tarea.paso_error(indice, 'No hay credenciales de PostgreSQL disponibles')
            resultados.append({'cliente': base_suelta, 'ok': False, 'error': 'sin credenciales'})
            continue
        resultados.append(_volcar(tarea, config, credenciales_servidor, base_suelta,
                                  base_suelta, indice))

    ok = sum(1 for r in resultados if r.get('ok'))
    tarea.log('Backups correctos: %s de %s' % (ok, len(resultados)),
              'ok' if ok == len(resultados) else 'aviso')
    tarea.datos['resultados'] = resultados
    if ok == 0:
        tarea.estado = 'error'


def verificar(tarea, config, archivo):
    """Tarea: comprueba que un backup existente esté completo y legible."""
    indice = tarea.paso('Verificar %s' % os.path.basename(archivo))
    if not ruta_valida(config, archivo):
        tarea.paso_error(indice, 'Archivo fuera de las carpetas de backups')
        tarea.estado = 'error'
        return
    tarea.log('  %s (%s)' % (archivo, bytes_legible(os.path.getsize(archivo))))
    ok, mensaje = comprobar(archivo)
    if ok:
        tarea.paso_ok(indice, mensaje)
    elif ok is None:
        tarea.paso_ok(indice)
        tarea.log('  %s' % mensaje, 'aviso')
    else:
        tarea.paso_error(indice, mensaje)
        tarea.estado = 'error'
    tarea.datos['resultados'] = [{'ok': bool(ok), 'archivo': archivo,
                                  'nombre': os.path.basename(archivo), 'mensaje': mensaje}]


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
