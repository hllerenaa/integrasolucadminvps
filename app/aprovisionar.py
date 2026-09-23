# -*- coding: utf-8 -*-
"""Asistente de creación de instancias pryinventario / pryrestaurante.

Reproduce lo que hacen new_instance_inventario.sh y new_instance_restaurante.sh,
pero sin preguntas interactivas y agregando lo que allí quedaba manual: el
servicio de systemd, el vhost de Apache y (opcional) el certificado.

Cada paso queda en el log de la tarea y todo lo que se crea se registra para
poder deshacerlo si algo falla.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

from . import units, webserver
from .credenciales import leer as leer_credenciales
from .tareas import TareaError
from .utils import bytes_legible, revisar_dns

RE_NOMBRE = re.compile(r'^[a-z][a-z0-9_-]{1,30}$')
RE_DB = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
RE_DOMINIO = re.compile(r'^[a-z0-9.-]{4,120}$')

EXCLUIR_COPIA = ('media/backups', '__pycache__', '*.pyc', '*.log')


# --------------------------------------------------------------------- ayudas
def _base_dir(config):
    """Carpeta donde viven las instancias (por defecto /home)."""
    cfg = config.get('aprovisionamiento') or {}
    return cfg.get('base_dir') or (config.get('base_dirs') or ['/home'])[0]


def _plantilla(config, tipo):
    plantillas = (config.get('aprovisionamiento') or {}).get('templates') or {}
    datos = plantillas.get(tipo)
    if not datos or not datos.get('ruta'):
        raise TareaError('No hay template configurado para "%s" (revisa '
                         '"aprovisionamiento.templates" en config.json)' % tipo)
    return datos


def _credenciales_template(config, tipo):
    plantilla = _plantilla(config, tipo)
    datos = leer_credenciales({'ruta': plantilla['ruta']}, con_secretos=True)
    if not datos.get('ok'):
        raise TareaError('No se pudo leer el credenciales.json del template: %s'
                         % datos.get('error'))
    return datos['datos']


def _entorno_pg(credenciales):
    entorno = dict(os.environ)
    entorno['PGPASSWORD'] = credenciales.get('POSTGRES_PASSWORD') or ''
    return entorno


def _psql_args(credenciales):
    return ['-h', credenciales.get('POSTGRES_HOST') or 'localhost',
            '-U', credenciales.get('POSTGRES_USER') or 'postgres']


def _psql_valor(credenciales, sql, base='postgres', timeout=20):
    """Ejecuta una consulta de un solo valor. Devuelve (ok, texto o error)."""
    comando = ['psql'] + _psql_args(credenciales) + ['-d', base, '-tAc', sql]
    try:
        proc = subprocess.run(comando, env=_entorno_pg(credenciales), timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        return False, 'psql no está instalado'
    except Exception as ex:
        return False, str(ex)
    if proc.returncode != 0:
        return False, (proc.stderr.decode('utf-8', 'replace').strip().splitlines() or ['error'])[0]
    return True, proc.stdout.decode('utf-8', 'replace').strip()


def _existe_base(config, tipo, nombre):
    """True/False si la base existe; None si no se pudo comprobar."""
    try:
        credenciales = _credenciales_template(config, tipo)
    except TareaError:
        return None
    if not RE_DB.match(nombre or '') and not re.match(r'^[A-Za-z0-9_]+$', nombre or ''):
        return None
    ok, valor = _psql_valor(credenciales, "SELECT 1 FROM pg_database WHERE datname='%s'" % nombre)
    if not ok:
        return None
    return valor == '1'


def _tamano_base(credenciales, nombre):
    ok, valor = _psql_valor(credenciales, 'SELECT pg_database_size(current_database())', nombre)
    return int(valor) if ok and valor.isdigit() else None


def _contar_tablas(credenciales, nombre):
    ok, valor = _psql_valor(credenciales, "SELECT COUNT(*) FROM information_schema.tables "
                                          "WHERE table_schema = 'public'", nombre)
    return int(valor) if ok and valor.isdigit() else None


def _libre(ruta):
    """Bytes libres en el disco donde está (o estará) la ruta."""
    while ruta and not os.path.exists(ruta):
        ruta = os.path.dirname(ruta)
    try:
        st = os.statvfs(ruta or '/')
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


def _tamano_carpeta(ruta):
    try:
        proc = subprocess.run(['du', '-sb', '--exclude=media/backups', ruta], timeout=120,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return int(proc.stdout.split()[0]) if proc.returncode == 0 else None
    except Exception:
        return None


def _puerto_escuchando(puerto, host='127.0.0.1'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, int(puerto))) == 0


def puertos_usados(config):
    """Puertos ya ocupados por otras instancias o escuchando en el servidor."""
    usados = set()
    for unidad in units.cargar_unidades(config):
        if unidad.get('puerto'):
            usados.add(unidad['puerto'])
    for vhost in webserver.cargar_vhosts(config):
        usados.update(vhost.get('puertos_proxy') or [])
    return usados


def puerto_libre(config, usados=None):
    cfg = config.get('aprovisionamiento') or {}
    inicio = int(cfg.get('puerto_inicial') or 8000)
    fin = int(cfg.get('puerto_final') or 8999)
    usados = set(usados if usados is not None else puertos_usados(config))
    for puerto in range(inicio, fin + 1):
        if puerto in usados:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(('127.0.0.1', puerto)) == 0:
                continue   # hay algo escuchando
        return puerto
    return None


def opciones(config, colector):
    """Datos para armar el formulario del asistente."""
    cfg = config.get('aprovisionamiento') or {}
    instancias = colector.snapshot()['instancias']
    modelos = [{'id': i['id'], 'cliente': i['cliente'], 'tipo': i['tipo'],
                'servicio': ((i.get('servicio_estado') or {}).get('archivo')
                             or i.get('servicio_archivo')),
                'vhost': (i.get('apache') or {}).get('archivo'),
                'puerto': i.get('puerto')}
               for i in instancias
               if ((i.get('servicio_estado') or {}).get('archivo') or i.get('servicio_archivo')
                   or (i.get('apache') or {}).get('archivo'))]
    usados = puertos_usados(config)
    return {
        'habilitado': bool(cfg.get('enabled', True)),
        'templates': {t: {'ruta': d.get('ruta'), 'db_origen': d.get('db_origen'),
                          'existe': os.path.isdir(d.get('ruta') or '')}
                      for t, d in (cfg.get('templates') or {}).items()},
        'dominio_base': cfg.get('dominio_base') or '',
        'base_dir': _base_dir(config),
        'venv': cfg.get('venv'),
        'puerto_sugerido': puerto_libre(config, usados),
        'puertos_usados': sorted(usados),
        'modelos': modelos,
        'modelo_servicio': cfg.get('modelo_servicio') or '',
        'modelo_vhost': cfg.get('modelo_vhost') or '',
        'certbot': bool((cfg.get('certbot') or {}).get('enabled')),
        'certbot_email': (cfg.get('certbot') or {}).get('email') or '',
        'actualizar_template': bool(cfg.get('actualizar_template', True)),
        'clientes': sorted({i['cliente'] for i in instancias}),
    }


# ------------------------------------------------------------------ validación
def validar(config, colector, datos):
    """Comprueba todo antes de tocar nada. Devuelve lista de resultados."""
    cfg = config.get('aprovisionamiento') or {}
    revisiones = []

    def revisar(ok, mensaje, critico=True, detalle=None):
        revisiones.append({'ok': bool(ok), 'mensaje': mensaje,
                           'critico': critico and not ok, 'detalle': detalle})

    tipo = datos.get('tipo')
    cliente = (datos.get('cliente') or '').strip().lower()
    base = (datos.get('base') or '').strip().lower()
    dominio = (datos.get('dominio') or '').strip().lower()
    puerto = datos.get('puerto')

    revisar(tipo in (config.get('proyectos') or {}), 'Sistema válido (%s)' % tipo)
    revisar(bool(RE_NOMBRE.match(cliente)),
            'Nombre de instancia válido: minúsculas, números, - o _ («%s»)' % cliente)
    revisar(bool(RE_DB.match(base)), 'Nombre de base válido («%s»)' % base)
    revisar(not dominio or bool(RE_DOMINIO.match(dominio)), 'Dominio válido («%s»)' % dominio)

    try:
        puerto = int(puerto)
        revisar(1024 < puerto < 65536, 'Puerto en rango (%s)' % puerto)
    except (TypeError, ValueError):
        revisar(False, 'Puerto inválido (%s)' % puerto)
        puerto = None

    if not RE_NOMBRE.match(cliente or ''):
        return revisiones

    carpeta = os.path.join(_base_dir(config), cliente)
    proyecto = (config.get('proyectos') or {}).get(tipo, '')
    revisar(not os.path.exists(carpeta), 'La carpeta %s está libre' % carpeta,
            detalle=None if not os.path.exists(carpeta) else 'Ya existe, elige otro nombre')

    plantilla = (cfg.get('templates') or {}).get(tipo) or {}
    ruta_template = plantilla.get('ruta') or ''
    revisar(os.path.isdir(ruta_template), 'Template disponible (%s)' % ruta_template)
    revisar(os.path.isfile(os.path.join(ruta_template, 'credenciales.json')),
            'El template tiene credenciales.json')

    revisar(os.path.isfile(os.path.join(ruta_template, 'manage.py')),
            'El template tiene manage.py')

    # PostgreSQL: sin conexión no se puede crear la base, así que es crítico.
    credenciales_tpl = None
    try:
        credenciales_tpl = _credenciales_template(config, tipo) if ruta_template else None
    except TareaError as ex:
        revisar(False, str(ex))
    if credenciales_tpl:
        conecta, detalle = _psql_valor(credenciales_tpl, 'SELECT version()')
        revisar(conecta, 'Conexión a PostgreSQL con el usuario del template (%s@%s)'
                % (credenciales_tpl.get('POSTGRES_USER'), credenciales_tpl.get('POSTGRES_HOST')),
                detalle=None if conecta else detalle)
        if conecta:
            existe = _existe_base(config, tipo, base) if RE_DB.match(base or '') else None
            if existe is not None:
                revisar(not existe, 'La base «%s» no existe todavía' % base)
            puede, valor = _psql_valor(
                credenciales_tpl, "SELECT rolcreatedb OR rolsuper FROM pg_roles "
                                  "WHERE rolname = current_user")
            revisar(puede and valor == 't', 'El usuario de PostgreSQL puede crear bases',
                    detalle=None if (puede and valor == 't') else 'Le falta el permiso CREATEDB')

            tamano_origen = None
            if plantilla.get('db_origen'):
                origen = _existe_base(config, tipo, plantilla['db_origen'])
                revisar(bool(origen), 'La base origen del dump existe (%s)' % plantilla['db_origen'])
                if origen:
                    tamano_origen = _tamano_base(credenciales_tpl, plantilla['db_origen'])

            # Espacio: la copia del template + el dump + la base restaurada.
            necesario = (_tamano_carpeta(ruta_template) or 0) + 2 * (tamano_origen or 0)
            libre = _libre(carpeta)
            if necesario and libre is not None:
                revisar(libre > necesario * 1.2,
                        'Espacio en disco suficiente (hacen falta ~%s, hay %s libres)'
                        % (bytes_legible(necesario), bytes_legible(libre)))
    else:
        revisar(False, 'No se pudieron leer las credenciales de PostgreSQL del template')

    unidades = {u['unidad'] for u in units.cargar_unidades(config)}
    revisar(cliente not in unidades, 'No hay un servicio systemd llamado «%s»' % cliente)

    if puerto:
        revisar(puerto not in puertos_usados(config), 'El puerto %s no está usado por otra instancia' % puerto)
        revisar(not _puerto_escuchando(puerto), 'Nada escucha ya en el puerto %s' % puerto)

    if dominio and RE_DOMINIO.match(dominio):
        chocan = [v['nombre'] for v in webserver.cargar_vhosts(config)
                  if (v.get('servername') or '').lower() == dominio
                  or dominio in [a.lower() for a in v.get('alias') or []]]
        revisar(not chocan, 'El dominio %s no está en otro vhost' % dominio,
                detalle=', '.join(chocan) if chocan else None)
        dns = revisar_dns(dominio)
        # Sin DNS la instancia se crea igual, pero certbot no podrá emitir.
        revisar(dns['ok'], dns['mensaje'], critico=False,
                detalle=None if dns['ok'] else ('certbot fallará hasta que el DNS apunte aquí'
                                                if datos.get('certbot') else 'la URL no responderá'))
    elif datos.get('crear_vhost', True):
        revisar(False, 'Sin dominio no se crea el vhost ni el certificado', critico=False)
    if datos.get('certbot') and not dominio:
        revisar(False, 'Pediste certificado pero no hay dominio', critico=False)

    venv = cfg.get('venv') or ''
    revisar(bool(venv) and os.path.isdir(venv), 'Entorno virtual disponible (%s)' % venv)
    if venv and os.path.isdir(venv):
        revisar(os.access(os.path.join(venv, 'bin', 'python'), os.X_OK),
                'El entorno virtual tiene bin/python')
        if datos.get('crear_servicio', True):
            revisar(os.access(os.path.join(venv, 'bin', 'gunicorn'), os.X_OK),
                    'El entorno virtual tiene gunicorn', critico=False)

    necesarias = ['pg_dump', 'psql']
    if datos.get('actualizar_template'):
        necesarias.append('git')
    if datos.get('crear_servicio', True):
        necesarias.append('systemctl')
    if dominio and datos.get('crear_vhost', True):
        necesarias.append('a2ensite')
    if dominio and datos.get('certbot'):
        necesarias.append('certbot')
    faltan = [h for h in necesarias if not shutil.which(h)]
    revisar(not faltan, 'Comandos necesarios instalados (%s)' % ', '.join(necesarias),
            detalle='faltan: %s' % ', '.join(faltan) if faltan else None)
    return revisiones


def diagnostico_entorno(config):
    """Revisa que el servidor tenga todo lo que usa el asistente, sin crear nada."""
    cfg = config.get('aprovisionamiento') or {}
    grupos = []

    def grupo(titulo):
        lista = []
        grupos.append({'titulo': titulo, 'revisiones': lista})

        def revisar(ok, mensaje, critico=True, detalle=None):
            lista.append({'ok': bool(ok), 'mensaje': mensaje,
                          'critico': critico and not ok, 'detalle': detalle})
        return revisar

    revisar = grupo('Herramientas del servidor')
    for herramienta, uso, critico in (
            ('pg_dump', 'volcar la base del template', True),
            ('psql', 'crear y restaurar la base', True),
            ('git', 'actualizar el template', False),
            ('rsync', 'copiar el template (si falta se copia con Python)', False),
            ('systemctl', 'crear el servicio', True),
            ('apache2ctl', 'validar el vhost', False),
            ('a2ensite', 'activar el vhost', False),
            ('certbot', 'emitir certificados', False)):
        ruta = shutil.which(herramienta)
        revisar(bool(ruta), '%s — %s' % (herramienta, uso), critico=critico, detalle=ruta)

    revisar = grupo('Entorno virtual de Python')
    venv = cfg.get('venv') or ''
    python = os.path.join(venv, 'bin', 'python')
    revisar(bool(venv) and os.path.isdir(venv), 'Carpeta del venv (%s)' % (venv or 'sin configurar'))
    if os.access(python, os.X_OK):
        try:
            proc = subprocess.run([python, '--version'], timeout=10,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            revisar(proc.returncode == 0, 'Python del venv responde',
                    detalle=proc.stdout.decode('utf-8', 'replace').strip())
            proc = subprocess.run([python, '-c', 'import django; print(django.get_version())'],
                                  timeout=20, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            lineas = proc.stdout.decode('utf-8', 'replace').strip().splitlines()
            revisar(proc.returncode == 0, 'Django instalado en el venv',
                    detalle=lineas[-1] if lineas else None)
        except Exception as ex:
            revisar(False, 'Python del venv responde', detalle=str(ex))
    else:
        revisar(False, 'Existe %s' % python)
    revisar(os.access(os.path.join(venv, 'bin', 'gunicorn'), os.X_OK),
            'gunicorn instalado en el venv', critico=False)

    for tipo, plantilla in (cfg.get('templates') or {}).items():
        revisar = grupo('Template de %s' % tipo)
        ruta = plantilla.get('ruta') or ''
        revisar(os.path.isdir(ruta), 'Carpeta %s' % (ruta or '(sin configurar)'))
        if not os.path.isdir(ruta):
            continue
        revisar(os.path.isfile(os.path.join(ruta, 'manage.py')), 'manage.py presente')
        revisar(os.path.isfile(os.path.join(ruta, 'credenciales.json')), 'credenciales.json presente')
        if os.path.isdir(os.path.join(ruta, '.git')) and shutil.which('git'):
            proc = subprocess.run(['git', '-C', ruta, 'status', '--porcelain'], timeout=20,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cambios = [l for l in proc.stdout.decode('utf-8', 'replace').splitlines() if l.strip()]
            revisar(not cambios, 'Sin cambios locales sin commitear', critico=False,
                    detalle=('%s archivo(s) modificados: se perderán al actualizar (git reset --hard)'
                             % len(cambios)) if cambios else None)
            proc = subprocess.run(['git', '-C', ruta, 'rev-parse', '--abbrev-ref', 'HEAD'], timeout=10,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            rama = proc.stdout.decode('utf-8', 'replace').strip()
            esperada = plantilla.get('rama') or 'master'
            revisar(rama == esperada, 'Rama %s (se espera %s)' % (rama or '?', esperada), critico=False)
            try:
                proc = subprocess.run(['git', '-C', ruta, 'ls-remote', '--heads', 'origin', esperada],
                                      timeout=20, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
                revisar(proc.returncode == 0 and bool(proc.stdout.strip()),
                        'El repositorio remoto responde (git ls-remote)', critico=False,
                        detalle=None if proc.returncode == 0 else
                        proc.stderr.decode('utf-8', 'replace').strip()[:200])
            except subprocess.TimeoutExpired:
                revisar(False, 'El repositorio remoto responde (git ls-remote)', critico=False,
                        detalle='sin respuesta en 20 s')
        try:
            credenciales = _credenciales_template(config, tipo)
        except TareaError as ex:
            revisar(False, 'Credenciales de PostgreSQL', detalle=str(ex))
            continue
        conecta, detalle = _psql_valor(credenciales, 'SHOW server_version')
        revisar(conecta, 'Conexión a PostgreSQL (%s@%s)' % (credenciales.get('POSTGRES_USER'),
                                                          credenciales.get('POSTGRES_HOST')),
                detalle=detalle)
        if conecta:
            puede, valor = _psql_valor(credenciales, "SELECT rolcreatedb OR rolsuper FROM pg_roles "
                                                     "WHERE rolname = current_user")
            revisar(puede and valor == 't', 'El usuario puede crear bases (CREATEDB)')
            origen = plantilla.get('db_origen')
            if origen:
                tamano = _tamano_base(credenciales, origen)
                revisar(tamano is not None, 'Base origen %s' % origen,
                        detalle=bytes_legible(tamano) if tamano is not None else 'no existe o sin acceso')
                if tamano is not None:
                    tablas = _contar_tablas(credenciales, origen)
                    revisar(bool(tablas), 'La base origen tiene tablas', detalle='%s tablas' % tablas)

    revisar = grupo('Carpetas y permisos')
    for etiqueta, ruta in (('Instancias', _base_dir(config)),
                           ('Servicios systemd', '/etc/systemd/system'),
                           ('Sitios de Apache', '/etc/apache2/sites-available')):
        revisar(os.path.isdir(ruta) and os.access(ruta, os.W_OK),
                '%s: se puede escribir en %s' % (etiqueta, ruta),
                critico=etiqueta != 'Sitios de Apache')
    libre = _libre(_base_dir(config))
    revisar(libre is None or libre > 2 * 1024 ** 3,
            'Espacio libre en %s' % _base_dir(config),
            detalle=bytes_legible(libre) if libre is not None else None)

    revisar = grupo('Puertos')
    usados = puertos_usados(config)
    inicio = int(cfg.get('puerto_inicial') or 8000)
    fin = int(cfg.get('puerto_final') or 8999)
    libres = sum(1 for p in range(inicio, fin + 1) if p not in usados)
    revisar(libres > 0, 'Puertos libres en el rango %s-%s' % (inicio, fin),
            detalle='%s libres, %s usados por instancias' % (libres, len(usados)))

    todas = [r for g in grupos for r in g['revisiones']]
    return {'grupos': grupos, 'ok': not any(r['critico'] for r in todas),
            'errores': sum(1 for r in todas if r['critico']),
            'avisos': sum(1 for r in todas if not r['ok'] and not r['critico'])}


# ------------------------------------------------------------------- plantillas
def _texto_plantilla(nombre):
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'deploy', 'plantillas')
    with open(os.path.join(base, nombre), 'r', encoding='utf-8') as fh:
        return fh.read()


def _clonar_de_modelo(archivo_modelo, reemplazos):
    """Toma un .service o vhost que ya funciona y sustituye los datos."""
    with open(archivo_modelo, 'r', encoding='utf-8') as fh:
        texto = fh.read()
    for viejo, nuevo in reemplazos:
        if viejo and nuevo and viejo != nuevo:
            texto = texto.replace(str(viejo), str(nuevo))
    return texto


def _modelo_de(colector, ident, clave):
    if not ident:
        return None
    datos = colector.instancia(ident)
    if not datos:
        return None
    if clave == 'servicio':
        archivo = ((datos.get('servicio_estado') or {}).get('archivo')
                   or datos.get('servicio_archivo'))
    else:
        archivo = (datos.get('apache') or {}).get('archivo')
    if not archivo or not os.path.isfile(archivo):
        return None
    return {'archivo': archivo, 'datos': datos}


# ------------------------------------------------------------------- ejecución
def crear_instancia(tarea, config, colector):
    """Corre todos los pasos de creación. Pensado para GestorTareas.lanzar()."""
    datos = tarea.datos
    cfg = config.get('aprovisionamiento') or {}
    simular = bool(datos.get('simular'))
    tipo = datos['tipo']
    cliente = datos['cliente'].strip().lower()
    base = datos['base'].strip().lower()
    dominio = (datos.get('dominio') or '').strip().lower()
    puerto = int(datos['puerto'])
    proyecto = (config.get('proyectos') or {})[tipo]
    plantilla = _plantilla(config, tipo)
    ruta_template = plantilla['ruta']
    destino_base = os.path.join(_base_dir(config), cliente)
    destino = os.path.join(destino_base, proyecto)
    venv = cfg.get('venv') or ''
    python = os.path.join(venv, 'bin', 'python') if venv else 'python3'
    credenciales_tpl = _credenciales_template(config, tipo)
    entorno_pg = _entorno_pg(credenciales_tpl)
    args_pg = _psql_args(credenciales_tpl)
    clave_pg = credenciales_tpl.get('POSTGRES_PASSWORD')

    if simular:
        tarea.log('MODO SIMULACIÓN: se valida y se muestran los comandos, no se ejecuta nada.', 'aviso')

    # 1 -------------------------------------------------------------- validar
    indice = tarea.paso('Validar prerequisitos')
    revisiones = validar(config, colector, datos)
    for revision in revisiones:
        tarea.log('  %s %s%s' % ('✔' if revision['ok'] else '✖', revision['mensaje'],
                                 ' — %s' % revision['detalle'] if revision.get('detalle') else ''),
                  'ok' if revision['ok'] else ('error' if revision['critico'] else 'aviso'))
    criticos = [r['mensaje'] for r in revisiones if r['critico']]
    if criticos:
        tarea.paso_error(indice, 'No se puede continuar')
        raise TareaError('Validaciones fallidas: %s' % '; '.join(criticos))
    tarea.paso_ok(indice, '%s comprobaciones correctas' % len(revisiones))

    # 2 --------------------------------------------------- actualizar template
    if datos.get('actualizar_template'):
        indice = tarea.paso('Actualizar el template desde git')
        rama = plantilla.get('rama') or 'master'
        tarea.ejecutar(['git', '-C', ruta_template, 'fetch', 'origin'], simular=simular)
        tarea.ejecutar(['git', '-C', ruta_template, 'reset', '--hard', 'origin/%s' % rama], simular=simular)
        tarea.ejecutar(['git', '-C', ruta_template, 'clean', '-fd'], critico=False, simular=simular)
        tarea.ejecutar([python, 'manage.py', 'makemigrations', '--noinput'],
                       cwd=ruta_template, critico=False, simular=simular)
        tarea.ejecutar([python, 'manage.py', 'migrate', '--noinput'],
                       cwd=ruta_template, simular=simular)
        tarea.paso_ok(indice, 'Template actualizado (%s)' % rama)
    else:
        tarea.log('Se omite la actualización del template por pedido del formulario', 'aviso')

    # 3 ------------------------------------------------------------ pg_dump
    indice = tarea.paso('Generar dump de la base origen')
    carpeta_backups = cfg.get('backups_dir') or os.path.join(ruta_template, 'media', 'backups')
    if not simular:
        os.makedirs(carpeta_backups, exist_ok=True)
    dump = os.path.join(carpeta_backups, 'template_%s_%s.sql' % (tipo, tarea.id))
    comando = ['pg_dump'] + args_pg + ['--exclude-table-data=django_migrations',
                                       '-f', dump, plantilla['db_origen']]
    tarea.ejecutar(comando, entorno=entorno_pg, timeout=3600, simular=simular, ocultar=[clave_pg])
    if not simular:
        tamano = os.path.getsize(dump) if os.path.isfile(dump) else 0
        if tamano < 1024:
            raise TareaError('El dump salió vacío o demasiado pequeño (%s bytes)' % tamano)
        tarea.paso_ok(indice, '%s (%.1f MB)' % (dump, tamano / 1048576.0))
    else:
        tarea.paso_ok(indice, dump)

    # 4 ------------------------------------------------------------ copiar
    indice = tarea.paso('Copiar el template a %s' % destino)
    if not simular:
        os.makedirs(destino_base, exist_ok=True)
        tarea.registrar_deshacer('carpeta', destino_base)
    if shutil.which('rsync'):
        comando = ['rsync', '-a']
        for patron in EXCLUIR_COPIA:
            comando += ['--exclude', patron]
        comando += [ruta_template.rstrip('/') + '/', destino.rstrip('/') + '/']
        tarea.ejecutar(comando, timeout=3600, simular=simular)
    else:
        # Sin rsync se copia desde Python para respetar las exclusiones
        # (cp -r no las soporta y arrastraría media/backups completo).
        tarea.log('$ copia con exclusiones: %s' % ', '.join(EXCLUIR_COPIA), 'cmd')
        if not simular:
            shutil.copytree(ruta_template, destino,
                            ignore=shutil.ignore_patterns(*[os.path.basename(p) for p in EXCLUIR_COPIA]),
                            symlinks=True, dirs_exist_ok=True)
            for patron in EXCLUIR_COPIA:
                if '/' in patron:
                    sobra = os.path.join(destino, patron)
                    if os.path.isdir(sobra):
                        shutil.rmtree(sobra, ignore_errors=True)
    tarea.paso_ok(indice, 'Copia lista')

    # 5 ------------------------------------------------------------ permisos
    indice = tarea.paso('Aplicar permisos')
    tarea.ejecutar(['chmod', '-R', '0775', destino], critico=False, simular=simular)
    if not simular:
        os.makedirs(os.path.join(destino, 'media'), exist_ok=True)
    tarea.ejecutar(['chmod', '-R', '0777', os.path.join(destino, 'media')],
                   critico=False, simular=simular)
    tarea.paso_ok(indice)

    # 6 ------------------------------------------------------- crear y restaurar
    indice = tarea.paso('Crear la base %s y restaurar el dump' % base)
    tarea.ejecutar(['psql'] + args_pg + ['-c', 'CREATE DATABASE %s;' % base, 'postgres'],
                   entorno=entorno_pg, simular=simular, ocultar=[clave_pg])
    if not simular:
        tarea.registrar_deshacer('base', base)
    codigo, salida = tarea.ejecutar(
        ['psql'] + args_pg + ['-q', '-v', 'ON_ERROR_STOP=0', '-d', base, '-f', dump],
        entorno=entorno_pg, timeout=7200, critico=False, simular=simular, ocultar=[clave_pg])
    if codigo != 0:
        tarea.log('  La restauración devolvió código %s; revisa los errores de arriba' % codigo, 'aviso')
    if not simular:
        # Con ON_ERROR_STOP=0 psql sigue ante errores: se cuentan y se compara
        # la cantidad de tablas con la base origen para saber si quedó completa.
        errores = [l for l in (salida or '').splitlines() if 'ERROR:' in l]
        if errores:
            tarea.log('  La restauración tuvo %s error(es); los primeros:' % len(errores), 'aviso')
            for linea in errores[:5]:
                tarea.log('    %s' % linea.strip(), 'aviso')
        tablas_origen = _contar_tablas(credenciales_tpl, plantilla['db_origen'])
        tablas_nueva = _contar_tablas(credenciales_tpl, base)
        tarea.log('  Tablas: origen %s · nueva %s' % (tablas_origen, tablas_nueva))
        if not tablas_nueva:
            tarea.paso_error(indice, 'La base quedó vacía')
            raise TareaError('La restauración no creó ninguna tabla en %s' % base)
        if tablas_origen and tablas_nueva < tablas_origen:
            tarea.log('  Faltan %s tabla(s) respecto al origen' % (tablas_origen - tablas_nueva), 'aviso')
        try:
            os.remove(dump)
            tarea.log('  Dump temporal eliminado (%s)' % dump)
        except OSError:
            pass
        tarea.paso_ok(indice, 'Base restaurada (%s tablas%s)'
                      % (tablas_nueva, ', %s errores' % len(errores) if errores else ''))
    else:
        tarea.paso_ok(indice, 'Base restaurada')

    # 7 -------------------------------------------------------- credenciales
    indice = tarea.paso('Configurar credenciales.json')
    archivo_credenciales = os.path.join(destino, 'credenciales.json')
    if not simular:
        with open(archivo_credenciales, 'r', encoding='utf-8') as fh:
            nuevas = json.load(fh)
        nuevas['POSTGRES_DBNAME'] = base
        if dominio:
            nuevas['DOMINIO_GENERAL'] = dominio
        nuevas['USE_SSL'] = bool(datos.get('certbot'))
        nuevas['DEBUG'] = bool(datos.get('debug', False))
        with open(archivo_credenciales, 'w', encoding='utf-8') as fh:
            json.dump(nuevas, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
    tarea.log('  POSTGRES_DBNAME=%s · DOMINIO_GENERAL=%s · USE_SSL=%s'
              % (base, dominio or '(sin cambio)', bool(datos.get('certbot'))))
    tarea.paso_ok(indice)

    # 8 ------------------------------------------------------------ migrate
    indice = tarea.paso('Sincronizar migraciones (migrate --fake)')
    tarea.ejecutar([python, 'manage.py', 'makemigrations', '--noinput'],
                   cwd=destino, critico=False, simular=simular)
    tarea.ejecutar([python, 'manage.py', 'migrate', '--fake', '--noinput'],
                   cwd=destino, simular=simular)
    tarea.paso_ok(indice)

    # 9 ------------------------------------------------------------ systemd
    if datos.get('crear_servicio', True):
        indice = tarea.paso('Crear el servicio systemd «%s»' % cliente)
        modelo = _modelo_de(colector, datos.get('modelo_servicio'), 'servicio')
        if modelo:
            base_datos_modelo = modelo['datos']
            texto = _clonar_de_modelo(modelo['archivo'], [
                (base_datos_modelo.get('ruta'), destino),
                (base_datos_modelo.get('puerto'), puerto),
                (base_datos_modelo.get('cliente'), cliente),
            ])
            tarea.log('  Clonado de %s' % modelo['archivo'])
        else:
            texto = (_texto_plantilla('gunicorn.service.tpl')
                     .replace('__CLIENTE__', cliente).replace('__SISTEMA__', tipo)
                     .replace('__RUTA__', destino).replace('__VENV__', venv)
                     .replace('__PUERTO__', str(puerto)).replace('__PROYECTO__', proyecto))
            tarea.log('  Generado desde la plantilla del panel')
        archivo_unidad = '/etc/systemd/system/%s.service' % cliente
        tarea.log('--- %s ---\n%s' % (archivo_unidad, texto))
        if not simular:
            with open(archivo_unidad, 'w', encoding='utf-8') as fh:
                fh.write(texto)
            tarea.registrar_deshacer('unidad', cliente)
        tarea.ejecutar(['systemctl', 'daemon-reload'], simular=simular)
        tarea.ejecutar(['systemctl', 'enable', '--now', cliente], simular=simular)
        tarea.paso_ok(indice, archivo_unidad)

    # 10 ------------------------------------------------------------- apache
    if datos.get('crear_vhost', True) and dominio:
        indice = tarea.paso('Crear el vhost de Apache para %s' % dominio)
        modelo = _modelo_de(colector, datos.get('modelo_vhost'), 'vhost')
        if modelo:
            datos_modelo = modelo['datos']
            texto = _clonar_de_modelo(modelo['archivo'], [
                (datos_modelo.get('ruta'), destino),
                (datos_modelo.get('puerto'), puerto),
                (datos_modelo.get('dominio'), dominio),
                (datos_modelo.get('cliente'), cliente),
            ])
            tarea.log('  Clonado de %s' % modelo['archivo'])
            if 'SSLCertificateFile' in texto:
                tarea.log('  El modelo trae SSL: certbot deberá regenerar el certificado', 'aviso')
        else:
            texto = (_texto_plantilla('vhost.conf.tpl')
                     .replace('__DOMINIO__', dominio).replace('__RUTA__', destino)
                     .replace('__PUERTO__', str(puerto)).replace('__CLIENTE__', cliente))
            tarea.log('  Generado desde la plantilla del panel')
        archivo_vhost = '/etc/apache2/sites-available/%s.conf' % cliente
        tarea.log('--- %s ---\n%s' % (archivo_vhost, texto))
        if not simular:
            with open(archivo_vhost, 'w', encoding='utf-8') as fh:
                fh.write(texto)
            tarea.registrar_deshacer('vhost', cliente)
        tarea.ejecutar(['apache2ctl', 'configtest'], critico=False, simular=simular)
        tarea.ejecutar(['a2ensite', cliente], simular=simular)
        tarea.ejecutar(['systemctl', 'reload', 'apache2'], simular=simular)
        tarea.paso_ok(indice, archivo_vhost)

    # 11 ------------------------------------------------------------ certbot
    if datos.get('certbot') and dominio:
        indice = tarea.paso('Emitir certificado SSL con certbot')
        correo = (cfg.get('certbot') or {}).get('email') or ''
        comando = ['certbot', '--apache', '-d', dominio, '--non-interactive', '--agree-tos',
                   '--redirect']
        comando += ['-m', correo] if correo else ['--register-unsafely-without-email']
        codigo, _ = tarea.ejecutar(comando, timeout=600, critico=False, simular=simular)
        if codigo == 0:
            tarea.paso_ok(indice, 'Certificado emitido')
        else:
            tarea.paso_error(indice, 'certbot falló; el sitio queda en HTTP')
            tarea.log('  Puedes reintentar a mano: %s' % ' '.join(comando), 'aviso')

    # 12 ----------------------------------------------------------- verificar
    indice = tarea.paso('Verificar la instancia')
    if simular:
        tarea.paso_ok(indice, 'Simulación: no se verifica nada real')
    else:
        fallos = verificar_instancia(tarea, cliente, puerto, dominio,
                                     con_servicio=datos.get('crear_servicio', True))
        colector.refrescar(forzar=True)
        nueva = colector.instancia('%s|%s' % (cliente, tipo))
        if not nueva:
            fallos.append('la instancia no aparece en el panel')
        else:
            db_ok = (nueva.get('db') or {}).get('ok')
            tarea.log('  %s Base de datos accesible desde la instancia' % ('✔' if db_ok else '✖'),
                      'ok' if db_ok else 'error')
            if not db_ok:
                fallos.append('la base no responde con credenciales.json')
            url = nueva.get('url_estado') or {}
            if dominio:
                tarea.log('  %s URL pública %s: %s'
                          % ('✔' if url.get('responde') else '!', url.get('url') or dominio,
                             ('HTTP %s' % url.get('codigo')) if url.get('responde')
                             else (url.get('error') or 'sin respuesta')),
                          'ok' if url.get('responde') else 'aviso')
        if fallos:
            # Se deja la tarea en «ok»: la instancia existe y el problema suele
            # ser de configuración. Deshacer sigue disponible desde Tareas.
            tarea.paso_error(indice, 'Creada, pero con problemas: %s' % '; '.join(fallos))
            tarea.datos['problemas'] = fallos
        else:
            tarea.paso_ok(indice, 'Instancia funcionando')
        tarea.datos['instancia_id'] = '%s|%s' % (cliente, tipo)

    tarea.log('Recuerda revisar el resto de credenciales.json (SMTP, tokens) '
              'y los datos de la empresa en el sistema.', 'aviso')


# ----------------------------------------------------------------- verificar
def verificar_instancia(tarea, cliente, puerto, dominio, con_servicio=True, espera=30):
    """Comprueba que la instancia recién creada realmente arrancó y responde.

    Devuelve la lista de fallos (vacía si todo está bien).
    """
    fallos = []
    if con_servicio:
        limite = time.time() + espera
        estado = ''
        while time.time() < limite:
            proc = subprocess.run(['systemctl', 'is-active', cliente], timeout=10,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            estado = proc.stdout.decode('utf-8', 'replace').strip()
            if estado == 'active':
                break
            time.sleep(2)
        tarea.log('  %s Servicio %s: %s' % ('✔' if estado == 'active' else '✖', cliente, estado or '?'),
                  'ok' if estado == 'active' else 'error')
        if estado != 'active':
            fallos.append('el servicio no quedó activo (revisa: journalctl -u %s -n 50)' % cliente)
            return fallos
    else:
        tarea.log('  No se creó el servicio: no se prueba si la aplicación responde', 'aviso')
        return fallos

    # gunicorn tarda unos segundos en abrir el puerto
    limite = time.time() + espera
    escucha = False
    while time.time() < limite:
        if _puerto_escuchando(puerto):
            escucha = True
            break
        time.sleep(1)
    tarea.log('  %s Puerto %s %s' % ('✔' if escucha else '✖', puerto,
                                     'escuchando' if escucha else 'sin respuesta'),
              'ok' if escucha else 'error')
    if not escucha:
        fallos.append('nada escucha en el puerto %s' % puerto)
        return fallos

    peticion = urllib.request.Request('http://127.0.0.1:%s/' % puerto, method='GET',
                                      headers={'Host': dominio or 'localhost',
                                               'User-Agent': 'integrasolucadminvps'})
    try:
        with urllib.request.urlopen(peticion, timeout=20) as respuesta:
            codigo = respuesta.getcode()
    except urllib.error.HTTPError as ex:
        codigo = ex.code
    except Exception as ex:
        codigo = None
        tarea.log('  ✖ La aplicación no respondió: %s' % ex, 'error')
    if codigo is not None:
        bien = codigo < 500
        tarea.log('  %s La aplicación responde en local: HTTP %s' % ('✔' if bien else '✖', codigo),
                  'ok' if bien else 'error')
        if not bien:
            fallos.append('la aplicación devuelve HTTP %s (revisa DEBUG/ALLOWED_HOSTS y el log)'
                          % codigo)
    else:
        fallos.append('la aplicación no responde en el puerto %s' % puerto)
    return fallos


# -------------------------------------------------------------------- deshacer
def deshacer(tarea, config, colector, tarea_destino):
    """Revierte lo que creó una tarea fallida (carpeta, base, unidad, vhost)."""
    acciones = list(reversed(tarea_destino.deshacer or []))
    if not acciones:
        tarea.log('La tarea no registró nada que deshacer', 'aviso')
        return

    tipo_sistema = (tarea_destino.datos or {}).get('tipo')
    for accion in acciones:
        valor = accion['valor']
        if accion['tipo'] == 'vhost':
            indice = tarea.paso('Quitar vhost %s' % valor)
            tarea.ejecutar(['a2dissite', valor], critico=False)
            archivo = '/etc/apache2/sites-available/%s.conf' % valor
            if os.path.isfile(archivo):
                os.remove(archivo)
                tarea.log('  Eliminado %s' % archivo)
            tarea.ejecutar(['systemctl', 'reload', 'apache2'], critico=False)
            tarea.paso_ok(indice)
        elif accion['tipo'] == 'unidad':
            indice = tarea.paso('Quitar servicio %s' % valor)
            tarea.ejecutar(['systemctl', 'disable', '--now', valor], critico=False)
            archivo = '/etc/systemd/system/%s.service' % valor
            if os.path.isfile(archivo):
                os.remove(archivo)
                tarea.log('  Eliminado %s' % archivo)
            tarea.ejecutar(['systemctl', 'daemon-reload'], critico=False)
            tarea.paso_ok(indice)
        elif accion['tipo'] == 'base':
            indice = tarea.paso('Eliminar la base %s' % valor)
            credenciales = _credenciales_template(config, tipo_sistema)
            tarea.ejecutar(['psql'] + _psql_args(credenciales) +
                           ['-c', 'DROP DATABASE IF EXISTS %s;' % valor, 'postgres'],
                           entorno=_entorno_pg(credenciales), critico=False,
                           ocultar=[credenciales.get('POSTGRES_PASSWORD')])
            tarea.paso_ok(indice)
        elif accion['tipo'] == 'carpeta':
            indice = tarea.paso('Eliminar la carpeta %s' % valor)
            # Sólo se borra lo que esta misma tarea creó bajo /home.
            normal = os.path.normpath(valor)
            base = os.path.normpath(_base_dir(config))
            # Sólo se acepta exactamente <base_dir>/<cliente>, nada más arriba.
            if os.path.dirname(normal) != base or normal == base:
                tarea.paso_error(indice, 'Ruta no permitida para borrado: %s' % normal)
                continue
            shutil.rmtree(normal, ignore_errors=True)
            tarea.log('  Eliminada %s' % normal)
            tarea.paso_ok(indice)

    colector.refrescar(forzar=True)
    tarea.log('Reversión terminada', 'ok')
