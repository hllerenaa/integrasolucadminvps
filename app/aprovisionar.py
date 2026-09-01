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

from . import units, webserver
from .credenciales import leer as leer_credenciales
from .tareas import TareaError

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


def _existe_base(config, tipo, nombre):
    """True/False si la base existe; None si no se pudo comprobar."""
    import subprocess
    credenciales = _credenciales_template(config, tipo)
    comando = ['psql'] + _psql_args(credenciales) + [
        '-tAc', "SELECT 1 FROM pg_database WHERE datname='%s'" % nombre, 'postgres']
    try:
        proc = subprocess.run(comando, env=_entorno_pg(credenciales), timeout=20,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode('utf-8', 'replace').strip() == '1'


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

    existe = _existe_base(config, tipo, base) if base else None
    if existe is None:
        revisar(False, 'No se pudo consultar PostgreSQL para verificar la base', critico=False,
                detalle='Se comprobará igual al crearla')
    else:
        revisar(not existe, 'La base «%s» no existe todavía' % base)

    if plantilla.get('db_origen'):
        origen = _existe_base(config, tipo, plantilla['db_origen'])
        if origen is not None:
            revisar(origen, 'La base origen del dump existe (%s)' % plantilla['db_origen'])

    unidades = {u['unidad'] for u in units.cargar_unidades(config)}
    revisar(cliente not in unidades, 'No hay un servicio systemd llamado «%s»' % cliente)

    if puerto:
        revisar(puerto not in puertos_usados(config), 'El puerto %s no está usado por otra instancia' % puerto)

    if dominio:
        chocan = [v['nombre'] for v in webserver.cargar_vhosts(config)
                  if (v.get('servername') or '').lower() == dominio
                  or dominio in [a.lower() for a in v.get('alias') or []]]
        revisar(not chocan, 'El dominio %s no está en otro vhost' % dominio,
                detalle=', '.join(chocan) if chocan else None)

    revisar(bool(cfg.get('venv')) and os.path.isdir(cfg.get('venv') or ''),
            'Entorno virtual disponible (%s)' % cfg.get('venv'))
    return revisiones


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
        ['psql'] + args_pg + ['-v', 'ON_ERROR_STOP=0', '-d', base, '-f', dump],
        entorno=entorno_pg, timeout=7200, critico=False, simular=simular, ocultar=[clave_pg])
    if codigo != 0:
        tarea.log('  La restauración devolvió código %s; revisa los errores de arriba' % codigo, 'aviso')
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
        colector.refrescar(forzar=True)
        nueva = colector.instancia('%s|%s' % (cliente, tipo))
        if not nueva:
            tarea.paso_error(indice, 'La instancia no aparece todavía en el panel')
        else:
            servicio = (nueva.get('servicio_estado') or {}).get('estado')
            url = (nueva.get('url_estado') or {})
            db_ok = (nueva.get('db') or {}).get('ok')
            tarea.log('  Servicio: %s · Base: %s · URL: %s'
                      % (servicio, 'ok' if db_ok else 'sin acceso',
                         ('HTTP %s' % url.get('codigo')) if url.get('responde') else 'sin respuesta'))
            tarea.paso_ok(indice, 'Instancia registrada en el panel')
        tarea.datos['instancia_id'] = '%s|%s' % (cliente, tipo)

    tarea.log('Recuerda revisar el resto de credenciales.json (SMTP, tokens) '
              'y los datos de la empresa en el sistema.', 'aviso')


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
