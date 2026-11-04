# -*- coding: utf-8 -*-
"""Asistente de creación de instancias pryinventario / pryrestaurante.

Sigue el flujo de new_instance_inventario.sh y new_instance_restaurante.sh, sin
preguntas interactivas, y agrega lo que allí quedaba manual: el socket y el
servicio de systemd, el vhost de Apache y (opcional) el certificado.

Cada paso queda en el log de la tarea. Lo que se crea se anota en un registro
para poder deshacerlo si algo falla o eliminar la instancia entera después.
"""
from __future__ import annotations

import glob
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time

from . import backups as mod_backups, certificados, units, webserver
from .credenciales import leer as leer_credenciales
from .tareas import TareaError
from .utils import ahora_iso, bytes_legible, revisar_dns

RE_NOMBRE = re.compile(r'^[a-z][a-z0-9_-]{1,30}$')
RE_DB = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
# Al menos un punto: «icecream» a secas no es un dominio y deja la URL rota.
RE_DOMINIO = re.compile(r'^(?=.{4,120}$)[a-z0-9-]+(\.[a-z0-9-]+)+$')

EXCLUIR_COPIA = ('media/backups', '__pycache__', '*.pyc', '*.log')

DIR_RUN = '/run'
DIR_UNIDADES = '/etc/systemd/system'
DIR_SITIOS_DISPONIBLES = '/etc/apache2/sites-available'
DIR_SITIOS_ACTIVOS = '/etc/apache2/sites-enabled'
DIR_LOGS_APACHE = '/var/log/apache2'
RUTA_OPCIONES_SSL = '/etc/letsencrypt/options-ssl-apache.conf'
MODULOS_APACHE = ('proxy_module', 'proxy_http_module', 'rewrite_module', 'ssl_module')


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


def _base_incompleta(credenciales, base, origen):
    """Compara la base con la de origen del template.

    Devuelve (incompleta, texto). Una restauración cortada deja menos tablas o
    las tablas sin datos (mucho más pequeña que el origen).
    """
    tablas = _contar_tablas(credenciales, base) or 0
    tablas_origen = _contar_tablas(credenciales, origen) if origen else None
    tamano = _tamano_base(credenciales, base)
    tamano_origen = _tamano_base(credenciales, origen) if origen else None
    texto = '%s tablas, %s' % (tablas, bytes_legible(tamano) if tamano is not None else '?')
    if tablas_origen:
        texto += ' · origen %s tablas, %s' % (
            tablas_origen, bytes_legible(tamano_origen) if tamano_origen is not None else '?')
    incompleta = bool(
        not tablas
        or (tablas_origen and tablas < tablas_origen)
        or (tamano is not None and tamano_origen and tamano < tamano_origen * 0.5))
    return incompleta, texto


def _valor_dominio(actual, dominio, ssl):
    """DOMINIO_GENERAL con el mismo formato que trae el template.

    new_instance_*.sh lo documenta como URL completa («https://dominio»); si el
    template lo tiene así se respeta (https con certificado, http sin él), y si
    lo tiene como dominio a secas se deja a secas.
    """
    actual = (actual or '').strip()
    if '://' in actual:
        return '%s://%s' % ('https' if ssl else 'http', dominio)
    return dominio


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


def normalizar_dominio(texto):
    """Deja sólo el dominio: de «https://admin.x.net:443/nueva?a=1» a «admin.x.net»."""
    valor = (texto or '').strip().lower()
    valor = re.sub(r'^[a-z][a-z0-9+.-]*://', '', valor)
    valor = re.split(r'[/?#\s]', valor, maxsplit=1)[0]
    valor = valor.rsplit('@', 1)[-1]
    valor = valor.split(':', 1)[0]
    return valor.strip('.')


def _ruta_socket(cliente):
    return '%s/%s.sock' % (DIR_RUN, cliente)


def _socket_responde(ruta):
    if not os.path.exists(ruta):
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(ruta) == 0


class _ConexionUnix(http.client.HTTPConnection):
    """Petición HTTP por el socket unix de gunicorn, igual que hace Apache."""

    def __init__(self, ruta, timeout=60):
        super().__init__('localhost', timeout=timeout)
        self._ruta = ruta

    def connect(self):
        conexion = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conexion.settimeout(self.timeout)
        conexion.connect(self._ruta)
        self.sock = conexion


def _vhost_propio(vhost, cliente, carpeta):
    """True si el vhost sirve esta instancia: apunta a su carpeta o a su socket.

    El nombre del archivo no basta: <dominio>.conf podría ser de otro cliente.
    """
    contenido = vhost.get('contenido') or ''
    return ((carpeta.rstrip('/') + '/') in contenido
            or _ruta_socket(cliente) in (vhost.get('sockets_unix') or []))


# -------------------------------------------------- registro de lo que crea el panel
# Sólo lo anotado aquí se puede eliminar desde el panel. Se escribe recurso por
# recurso para que un alta interrumpida también quede cubierta.
_lock_registro = threading.Lock()


def _ruta_registro(config):
    return os.path.join(config.var_dir, 'aprovisionadas.json')


def _registro_leer(config):
    try:
        with open(_ruta_registro(config), 'r', encoding='utf-8') as fh:
            datos = json.load(fh)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _registro_escribir(config, datos):
    ruta = _ruta_registro(config)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    temporal = '%s.%s.tmp' % (ruta, os.getpid())
    with open(temporal, 'w', encoding='utf-8') as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=2)
    os.replace(temporal, ruta)


def registro_listar(config):
    with _lock_registro:
        return _registro_leer(config)


def registro_obtener(config, cliente):
    return registro_listar(config).get(cliente)


def registro_crear(config, cliente, **campos):
    with _lock_registro:
        datos = _registro_leer(config)
        datos.setdefault(cliente, {'cliente': cliente, 'base': None, 'unidad': None,
                                   'socket': None, 'sitio': None, 'certificado': None,
                                   'ssl_pendiente': False})
        datos[cliente].update(campos)
        _registro_escribir(config, datos)


def registro_actualizar(config, cliente, **campos):
    """Actualiza una entrada; no crea nada si la instancia no es del panel."""
    with _lock_registro:
        datos = _registro_leer(config)
        if cliente in datos:
            datos[cliente].update(campos)
            _registro_escribir(config, datos)


def registro_quitar(config, cliente):
    with _lock_registro:
        datos = _registro_leer(config)
        if datos.pop(cliente, None) is not None:
            _registro_escribir(config, datos)


def instancias_registradas(config, gestor=None):
    """Lista para la sección «Creadas desde el panel», con su estado."""
    resultado = []
    for cliente, entrada in sorted(registro_listar(config).items()):
        fila = dict(entrada)
        tarea = gestor.obtener(entrada.get('tarea')) if gestor and entrada.get('tarea') else None
        fila['tarea_estado'] = tarea.estado if tarea else None
        fila['carpeta_existe'] = os.path.isdir(entrada.get('carpeta') or '')
        fila['socket_activo'] = False
        try:
            fila['socket_activo'] = _socket_responde(_ruta_socket(cliente))
        except (OSError, AttributeError):
            pass
        resultado.append(fila)
    return resultado


def _dominio_base(config, instancias):
    """dominio_base de config.json o, si falta, el sufijo más usado por las instancias."""
    fijo = (config.get('aprovisionamiento') or {}).get('dominio_base')
    if fijo:
        return fijo.strip().lower().strip('.')
    conteo = {}
    for inst in instancias:
        partes = (inst.get('dominio') or '').lower().split('.')
        if len(partes) >= 3:
            sufijo = '.'.join(partes[1:])
            conteo[sufijo] = conteo.get(sufijo, 0) + 1
    return max(conteo, key=conteo.get) if conteo else ''


def _previo(config, tipo, cliente, base):
    """Lo que ya existe de un intento anterior con ese nombre (para reanudar)."""
    proyecto = (config.get('proyectos') or {}).get(tipo, '')
    carpeta = os.path.join(_base_dir(config), cliente)
    destino = os.path.join(carpeta, proyecto) if proyecto else carpeta
    previo = {'carpeta': carpeta, 'destino': destino,
              'carpeta_existe': os.path.exists(carpeta),
              'copia_completa': (os.path.isfile(os.path.join(destino, 'manage.py'))
                                 and os.path.isfile(os.path.join(destino, 'credenciales.json'))),
              'db_credenciales': None, 'unidad': None, 'vhosts': []}
    if previo['copia_completa']:
        try:
            with open(os.path.join(destino, 'credenciales.json'), 'r', encoding='utf-8') as fh:
                previo['db_credenciales'] = (json.load(fh) or {}).get('POSTGRES_DBNAME')
        except (OSError, ValueError):
            pass
    for unidad in units.cargar_unidades(config):
        if unidad['unidad'] == cliente:
            previo['unidad'] = unidad
    for vhost in webserver.cargar_vhosts(config):
        if vhost.get('servidor') == 'apache' and _vhost_propio(vhost, cliente, carpeta):
            previo['vhosts'].append(vhost)
    return previo


def _es_propia(previo, ruta):
    """True si la ruta (WorkingDirectory) cae dentro de la carpeta de esta instancia."""
    if not ruta:
        return False
    ruta = os.path.normpath(ruta)
    return ruta == os.path.normpath(previo['carpeta']) or ruta.startswith(
        os.path.normpath(previo['carpeta']) + os.sep)


def opciones(config, colector):
    """Datos para armar el formulario del asistente."""
    cfg = config.get('aprovisionamiento') or {}
    instancias = colector.snapshot()['instancias']
    return {
        'habilitado': bool(cfg.get('enabled', True)),
        'templates': {t: {'ruta': d.get('ruta'), 'db_origen': d.get('db_origen'),
                          'existe': os.path.isdir(d.get('ruta') or '')}
                      for t, d in (cfg.get('templates') or {}).items()},
        'dominio_base': _dominio_base(config, instancias),
        'base_dir': _base_dir(config),
        'venv': cfg.get('venv'),
        'certbot': bool((cfg.get('certbot') or {}).get('enabled')),
        'certbot_email': (cfg.get('certbot') or {}).get('email') or '',
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
    dominio = normalizar_dominio(datos.get('dominio'))
    reanudar = bool(datos.get('reanudar'))
    pista = 'Si viene de un intento anterior, marca «Reanudar / corregir»'

    revisar(tipo in (config.get('proyectos') or {}), 'Sistema válido (%s)' % tipo)
    revisar(bool(RE_NOMBRE.match(cliente)),
            'Nombre de instancia válido: minúsculas, números, - o _ («%s»)' % cliente)
    revisar(bool(RE_DB.match(base)), 'Nombre de base válido («%s»)' % base)
    revisar(not dominio or bool(RE_DOMINIO.match(dominio)), 'Dominio válido («%s»)' % dominio,
            detalle=None if not dominio or RE_DOMINIO.match(dominio)
            else 'Debe ser un dominio completo, p. ej. %s.%s'
                 % (cliente or 'cliente', _dominio_base(config, []) or 'integrasoluc.net'))

    if not RE_NOMBRE.match(cliente or ''):
        return revisiones

    carpeta = os.path.join(_base_dir(config), cliente)
    previo = _previo(config, tipo, cliente, base)
    if reanudar:
        revisar(True, 'Modo reanudar / corregir: se reutiliza lo que ya existe de «%s» y sólo '
                      'se crea lo que falta' % cliente)
    if not previo['carpeta_existe']:
        revisar(True, 'La carpeta %s está libre' % carpeta)
    elif not reanudar:
        revisar(False, 'La carpeta %s ya existe' % carpeta,
                detalle='%s; si no, elige otro nombre' % pista)
    elif previo['copia_completa'] and previo['db_credenciales'] not in (None, base):
        revisar(False, 'La carpeta %s es de esta instancia' % carpeta,
                detalle='su credenciales.json usa la base «%s», no «%s»'
                        % (previo['db_credenciales'], base))
    else:
        revisar(True, 'La carpeta %s ya existe: %s' % (
            carpeta, 'se reutiliza la copia' if previo['copia_completa']
            else 'copia incompleta, se vuelve a copiar el template'))

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
            if existe and reanudar:
                incompleta, resumen = _base_incompleta(credenciales_tpl, base,
                                                       plantilla.get('db_origen'))
                if datos.get('recrear_base'):
                    revisar(True, 'La base «%s» ya existe: se borra y se vuelve a restaurar desde '
                                  'el backup de %s' % (base, plantilla.get('db_origen')),
                            detalle=resumen)
                elif incompleta:
                    revisar(False, 'La base «%s» ya existe pero parece incompleta' % base,
                            detalle='%s. Marca «Recrear la base desde el backup del template»' % resumen)
                else:
                    revisar(True, 'La base «%s» ya existe y está completa: se reutiliza' % base,
                            detalle=resumen)
            elif existe is not None:
                # El texto describe lo encontrado: «✖ no existe todavía» se leía al revés.
                revisar(not existe, ('La base «%s» ya existe (de un intento anterior o de otra instancia)'
                                     if existe else 'La base «%s» no existe todavía') % base,
                        detalle=pista if existe else None)
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

    unidad_previa = previo['unidad']
    if not unidad_previa:
        revisar(True, 'No hay un servicio systemd llamado «%s»' % cliente)
    elif not reanudar:
        revisar(False, 'Ya existe un servicio systemd llamado «%s»' % cliente, detalle=pista)
    elif not _es_propia(previo, unidad_previa.get('workingdirectory')):
        revisar(False, 'El servicio «%s» es de esta instancia' % cliente,
                detalle='su WorkingDirectory es %s' % unidad_previa.get('workingdirectory'))
    else:
        revisar(True, 'El servicio «%s» ya existe: se reescribe y se reinicia' % cliente)

    # Cada instancia tiene su propio socket unix, igual que las de producción.
    socket_previo = units.cargar_sockets(config).get(cliente)
    if not socket_previo:
        revisar(True, 'No hay un socket systemd llamado «%s»' % cliente)
    elif not reanudar:
        revisar(False, 'Ya existe un socket systemd llamado «%s»' % cliente, detalle=pista)
    elif socket_previo.get('rutas') != [_ruta_socket(cliente)]:
        revisar(False, 'El socket «%s» es de esta instancia' % cliente,
                detalle='escucha en %s' % ', '.join(socket_previo.get('escuchas') or ['?']))
    else:
        revisar(True, 'El socket «%s» ya existe: se reescribe' % cliente)

    if dominio and RE_DOMINIO.match(dominio):
        chocan = [v['nombre'] for v in webserver.cargar_vhosts(config)
                  if not (reanudar and _vhost_propio(v, cliente, carpeta))
                  and ((v.get('servername') or '').lower() == dominio
                       or dominio in [a.lower() for a in v.get('alias') or []])]
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

    necesarias = ['git', 'pg_dump', 'psql']
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


def validar_campo(config, campo, datos):
    """Revisión rápida de un solo campo mientras se escribe en el formulario.

    Devuelve {campo, nivel: ok|aviso|error, mensaje, valor}. `valor` es el dato
    ya limpio, para que el formulario lo muestre como se va a usar.
    """
    cliente = (datos.get('cliente') or '').strip().lower()
    reanudar = bool(datos.get('reanudar'))
    valor = (datos.get('valor') or '').strip().lower()

    def respuesta(nivel, mensaje):
        return {'campo': campo, 'nivel': nivel, 'mensaje': mensaje, 'valor': valor}

    if campo == 'cliente':
        if not RE_NOMBRE.match(valor):
            return respuesta('error', 'Usa minúsculas, números, - o _ y empieza con una letra')
        if reanudar:
            return respuesta('ok', 'Modo reanudar: se reutiliza lo que ya exista de «%s»' % valor)
        carpeta = os.path.join(_base_dir(config), valor)
        if os.path.exists(carpeta):
            return respuesta('error', 'Ya existe la carpeta %s' % carpeta)
        if any(u['unidad'] == valor for u in units.cargar_unidades(config)):
            return respuesta('error', 'Ya existe un servicio systemd llamado «%s»' % valor)
        if valor in units.cargar_sockets(config):
            return respuesta('error', 'Ya existe un socket systemd llamado «%s»' % valor)
        return respuesta('ok', 'Disponible. Socket: %s' % _ruta_socket(valor))

    if campo == 'base':
        if not RE_DB.match(valor):
            return respuesta('error', 'Usa minúsculas, números o _ y empieza con una letra')
        if reanudar:
            return respuesta('ok', 'Modo reanudar: la validación completa revisa esta base')
        existe = _existe_base(config, datos.get('tipo'), valor)
        if existe:
            return respuesta('error', 'La base «%s» ya existe' % valor)
        if existe is None:
            return respuesta('aviso', 'No se pudo comprobar en PostgreSQL')
        return respuesta('ok', 'Disponible')

    if campo == 'dominio':
        valor = normalizar_dominio(valor)
        if not valor:
            return respuesta('aviso', 'Sin dominio no se crea el vhost ni el certificado')
        if not RE_DOMINIO.match(valor):
            return respuesta('error', 'Debe ser un dominio completo, p. ej. cliente.%s'
                             % (_dominio_base(config, []) or 'integrasoluc.net'))
        carpeta = os.path.join(_base_dir(config), cliente) if cliente else ''
        chocan = [v for v in webserver.cargar_vhosts(config)
                  if ((v.get('servername') or '').lower() == valor
                      or valor in [a.lower() for a in v.get('alias') or []])
                  and not (reanudar and carpeta and _vhost_propio(v, cliente, carpeta))]
        if chocan:
            return respuesta('error', 'Ya lo usa el sitio %s' % ', '.join(sorted(
                {v.get('nombre') or v.get('archivo') or '?' for v in chocan})))
        avisos = []
        if os.path.isdir(os.path.join(certificados.RUTA_LIVE, valor)):
            avisos.append('ya existe un certificado para este dominio')
        dns = revisar_dns(valor)
        if not dns['ok']:
            avisos.append(dns['mensaje'])
        if avisos:
            return respuesta('aviso', '; '.join(avisos))
        return respuesta('ok', 'Disponible y el DNS apunta a este servidor')

    return respuesta('error', 'Campo desconocido')


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
            ('git', 'actualizar el template', True),
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

    revisar = grupo('Apache')
    if shutil.which('apache2ctl'):
        proc = subprocess.run(['apache2ctl', '-M'], timeout=20,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cargados = proc.stdout.decode('utf-8', 'replace')
        for modulo in MODULOS_APACHE:
            revisar(modulo in cargados, 'Módulo %s activo' % modulo)
    revisar(os.path.isfile(RUTA_OPCIONES_SSL), 'Existe %s' % RUTA_OPCIONES_SSL, critico=False)

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


def texto_vhost(cliente, dominio, ruta, ssl=False):
    """Un solo .conf: en HTTP, o con redirección a HTTPS y el bloque 443."""
    texto = _texto_plantilla('vhost-ssl.conf.tpl' if ssl else 'vhost.conf.tpl')
    return (texto.replace('__DOMINIO__', dominio).replace('__RUTA__', ruta.rstrip('/'))
            .replace('__CLIENTE__', cliente).replace('__SOCKET__', _ruta_socket(cliente)))


def texto_servicio(cliente, ruta, venv, proyecto):
    return (_texto_plantilla('gunicorn.service.tpl')
            .replace('__CLIENTE__', cliente).replace('__RUTA__', ruta)
            .replace('__VENV__', venv).replace('__SOCKET__', _ruta_socket(cliente))
            .replace('__PROYECTO__', proyecto))


def texto_socket(cliente):
    return (_texto_plantilla('gunicorn.socket.tpl')
            .replace('__CLIENTE__', cliente).replace('__SOCKET__', _ruta_socket(cliente)))


def _archivo_vhost(sitio):
    return os.path.join(DIR_SITIOS_DISPONIBLES, '%s.conf' % sitio)


def _recargar_apache(tarea, simular=False):
    """Valida antes de recargar: un error nunca debe tumbar los demás sitios."""
    codigo, _ = tarea.ejecutar(['apache2ctl', 'configtest'], critico=False, simular=simular)
    if codigo != 0:
        return False
    codigo, _ = tarea.ejecutar(['systemctl', 'reload', 'apache2'], critico=False, simular=simular)
    return codigo == 0


def _escribir_vhost_activo(tarea, sitio, texto, respaldo=None, simular=False):
    """Escribe y activa el vhost. Si Apache lo rechaza, no lo deja activo."""
    archivo = _archivo_vhost(sitio)
    tarea.log('--- %s ---\n%s' % (archivo, texto))
    if not simular:
        with open(archivo, 'w', encoding='utf-8') as fh:
            fh.write(texto)
    tarea.ejecutar(['a2ensite', sitio], simular=simular)
    if _recargar_apache(tarea, simular):
        return True
    if respaldo is not None:
        tarea.log('  Apache rechazó la configuración: se restaura la anterior', 'error')
        if not simular:
            with open(archivo, 'w', encoding='utf-8') as fh:
                fh.write(respaldo)
        _recargar_apache(tarea, simular)
    else:
        tarea.log('  Apache rechazó la configuración: el sitio se desactiva', 'error')
        tarea.ejecutar(['a2dissite', sitio], critico=False, simular=simular)
    return False


def _actualizar_credenciales(archivo, cambios=None, dominio=None, ssl=False):
    with open(archivo, 'r', encoding='utf-8') as fh:
        datos = json.load(fh)
    datos.update(cambios or {})
    if dominio:
        datos['DOMINIO_GENERAL'] = _valor_dominio(datos.get('DOMINIO_GENERAL'), dominio, ssl)
    datos['USE_SSL'] = bool(ssl)
    with open(archivo, 'w', encoding='utf-8') as fh:
        json.dump(datos, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    return datos


def _comando_certbot(config, dominio):
    correo = ((config.get('aprovisionamiento') or {}).get('certbot') or {}).get('email') or ''
    # certonly: emite sin tocar los .conf. El hook recarga Apache al renovar.
    comando = ['certbot', 'certonly', '--apache', '-d', dominio, '--cert-name', dominio,
               '--non-interactive', '--agree-tos', '--keep-until-expiring',
               '--deploy-hook', 'systemctl reload apache2']
    return comando + (['-m', correo] if correo else ['--register-unsafely-without-email'])


def _configurar_ssl(tarea, config, cliente, dominio, destino, con_servicio=True, simular=False):
    """Emite el certificado y deja un único .conf con HTTPS. True si quedó en HTTPS."""
    indice = tarea.paso('Emitir certificado SSL para %s' % dominio)
    ya_existia = os.path.isdir(os.path.join(certificados.RUTA_LIVE, dominio))
    comando = _comando_certbot(config, dominio)
    codigo, _ = tarea.ejecutar(comando, timeout=600, critico=False, simular=simular)
    if codigo != 0:
        tarea.paso_error(indice, 'certbot falló; el sitio sigue en HTTP')
        return False
    if not simular and not ya_existia:
        tarea.registrar_deshacer('certificado', dominio)
        registro_actualizar(config, cliente, certificado=dominio)
    tarea.paso_ok(indice, 'Certificado disponible')

    indice = tarea.paso('Configurar HTTPS en %s.conf' % dominio)
    texto = texto_vhost(cliente, dominio, destino, ssl=True)
    if not simular and not os.path.isfile(RUTA_OPCIONES_SSL):
        texto = texto.replace('    Include %s\n' % RUTA_OPCIONES_SSL, '')
    respaldo = texto_vhost(cliente, dominio, destino, ssl=False)
    if not _escribir_vhost_activo(tarea, dominio, texto, respaldo=respaldo, simular=simular):
        tarea.paso_error(indice, 'Apache no aceptó la configuración SSL; el sitio sigue en HTTP')
        return False
    if not simular:
        _actualizar_credenciales(os.path.join(destino, 'credenciales.json'),
                                 dominio=dominio, ssl=True)
    if con_servicio:
        tarea.ejecutar(['systemctl', 'restart', '%s.service' % cliente], simular=simular)
    tarea.paso_ok(indice, 'Sitio en HTTPS')
    return True


# ------------------------------------------------------------------- ejecución
def crear_instancia(tarea, config, colector):
    """Corre todos los pasos de creación. Pensado para GestorTareas.lanzar().

    Los pasos 2 a 8 son los de new_instance_*.sh, en su orden; después vienen
    los que el script dejaba como manuales: servicio, vhost y certificado.
    """
    datos = tarea.datos
    cfg = config.get('aprovisionamiento') or {}
    simular = bool(datos.get('simular'))
    tipo = datos['tipo']
    cliente = datos['cliente'].strip().lower()
    base = datos['base'].strip().lower()
    dominio = normalizar_dominio(datos.get('dominio'))
    datos['dominio'] = dominio
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
    con_servicio = datos.get('crear_servicio', True)
    marca = time.strftime('%Y%m%d_%H%M%S')

    reanudar = bool(datos.get('reanudar'))

    if simular:
        tarea.log('MODO SIMULACIÓN: se valida y se muestran los comandos, no se ejecuta nada.', 'aviso')
    if reanudar:
        tarea.log('MODO REANUDAR / CORREGIR: se reutiliza lo que ya existe y se rehace la '
                  'configuración (credenciales, servicio, vhost).', 'aviso')

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

    # Qué quedó de un intento anterior (sólo cuenta en modo reanudar).
    previo = _previo(config, tipo, cliente, base)
    base_existia = bool(reanudar and _existe_base(config, tipo, base))
    tablas_existentes = (_contar_tablas(credenciales_tpl, base) or 0) if base_existia else 0
    reutilizar_copia = bool(reanudar and previo['copia_completa'])
    unidad_existia = bool(reanudar and previo['unidad'])
    vhost_existia = bool(reanudar and any(v.get('sitio') == dominio for v in previo['vhosts']))
    recrear = bool(base_existia and datos.get('recrear_base'))
    restaurar = recrear or not tablas_existentes

    # Sólo entra al registro lo que nace aquí: reanudar sobre una carpeta que ya
    # existía nunca vuelve «eliminable» una instancia anterior al registro.
    if not simular:
        if not previo['carpeta_existe']:
            registro_crear(config, cliente, tipo=tipo, carpeta=destino_base, proyecto=proyecto,
                           dominio=dominio, tarea=tarea.id, creado=ahora_iso(),
                           creado_por=tarea.creado_por)
        else:
            registro_actualizar(config, cliente, dominio=dominio)

    # 2 --------------------------------------------------- actualizar template
    if reutilizar_copia and not restaurar:
        tarea.log('Se reutilizan la copia y la base: no hace falta actualizar el template', 'aviso')
    else:
        indice = tarea.paso('Actualizar el template desde git')
        rama = plantilla.get('rama') or 'master'
        tarea.ejecutar(['git', '-C', ruta_template, 'fetch', 'origin'], simular=simular)
        tarea.ejecutar(['git', '-C', ruta_template, 'reset', '--hard', 'origin/%s' % rama], simular=simular)
        tarea.ejecutar(['git', '-C', ruta_template, 'clean', '-fd'], critico=False, simular=simular)
        tarea.ejecutar([python, 'manage.py', 'makemigrations', '--noinput'],
                       cwd=ruta_template, simular=simular)
        tarea.ejecutar([python, 'manage.py', 'migrate', '--noinput'],
                       cwd=ruta_template, simular=simular)
        tarea.paso_ok(indice, 'Template actualizado (%s)' % rama)

    # 3 ------------------------------------------------------------ pg_dump
    indice = tarea.paso('Generar dump de la base origen')
    dump = None
    carpeta_backups = cfg.get('backups_dir') or os.path.join(ruta_template, 'media', 'backups')
    if not restaurar:
        tarea.paso_ok(indice, 'No hace falta: la base %s ya tiene %s tablas' % (base, tablas_existentes))
    else:
        if not simular:
            os.makedirs(carpeta_backups, exist_ok=True)
        dump = os.path.join(carpeta_backups, 'template_%s_%s.sql' % (tipo, marca))
        comando = ['pg_dump'] + args_pg + ['--exclude-table-data=django_migrations',
                                           '-f', dump, plantilla['db_origen']]
        codigo, salida = tarea.ejecutar(comando, entorno=entorno_pg, timeout=3600, critico=False,
                                        simular=simular, ocultar=[clave_pg])
        if codigo != 0:
            with open('%s.err' % dump, 'w', encoding='utf-8') as fh:
                fh.write(salida or '')
            tarea.paso_error(indice, 'pg_dump falló; revisa %s.err' % dump)
            raise TareaError('Falló pg_dump de %s' % plantilla['db_origen'])
        if not simular:
            tamano = os.path.getsize(dump) if os.path.isfile(dump) else 0
            if tamano < 1024:
                raise TareaError('El dump salió vacío o demasiado pequeño (%s bytes)' % tamano)
            tarea.paso_ok(indice, '%s (%.1f MB)' % (dump, tamano / 1048576.0))
        else:
            tarea.paso_ok(indice, dump)

    # 4 ------------------------------------------------------------ copiar
    indice = tarea.paso('Copiar el template a %s' % destino)
    if reutilizar_copia:
        tarea.paso_ok(indice, 'Ya hay una copia completa del intento anterior: se reutiliza')
    else:
        if not simular:
            os.makedirs(destino_base, exist_ok=True)
            # Sólo se deshace lo que crea esta tarea, nunca una carpeta previa.
            if not previo['carpeta_existe']:
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
    if not restaurar:
        tarea.paso_ok(indice, 'La base ya existe con %s tablas: se reutiliza' % tablas_existentes)
    else:
        if recrear:
            # La instancia no debe tener conexiones abiertas para poder borrar la base.
            if previo['unidad']:
                tarea.ejecutar(['systemctl', 'stop', '%s.socket' % cliente], critico=False, simular=simular)
                tarea.ejecutar(['systemctl', 'stop', cliente], critico=False, simular=simular)
            _cerrar_conexiones(tarea, credenciales_tpl, base, simular)
            tarea.ejecutar(['psql'] + args_pg + ['-c', 'DROP DATABASE IF EXISTS %s;' % base, 'postgres'],
                           entorno=entorno_pg, simular=simular, ocultar=[clave_pg])
            tarea.ejecutar(['psql'] + args_pg + ['-c', 'CREATE DATABASE %s;' % base, 'postgres'],
                           entorno=entorno_pg, simular=simular, ocultar=[clave_pg])
        elif base_existia:
            tarea.log('  La base %s ya existe pero está vacía: sólo se restaura' % base)
        else:
            tarea.ejecutar(['psql'] + args_pg + ['-c', 'CREATE DATABASE %s;' % base, 'postgres'],
                           entorno=entorno_pg, simular=simular, ocultar=[clave_pg])
            if not simular:
                tarea.registrar_deshacer('base', base)
                registro_actualizar(config, cliente, base=base)
        codigo, salida = tarea.ejecutar(
            ['psql'] + args_pg + ['-q', '-v', 'ON_ERROR_STOP=0', '-d', base, '-f', dump],
            entorno=entorno_pg, timeout=7200, critico=False, simular=simular, ocultar=[clave_pg])
        if codigo != 0:
            tarea.log('  La restauración devolvió código %s; revisa los errores de arriba' % codigo, 'aviso')
        if not simular:
            log_restore = os.path.join(carpeta_backups, 'restore_%s_%s.log' % (base, marca))
            try:
                with open(log_restore, 'w', encoding='utf-8') as fh:
                    fh.write(salida or '')
                tarea.log('  Log de la restauración: %s' % log_restore)
            except OSError as ex:
                tarea.log('  No se pudo guardar el log de la restauración: %s' % ex, 'aviso')
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
                tarea.paso_error(indice, 'Faltan %s tabla(s) respecto al origen'
                                 % (tablas_origen - tablas_nueva))
                raise TareaError('La base %s quedó incompleta: usa Deshacer o Reanudar con '
                                 '«Recrear la base»' % base)
            tarea.paso_ok(indice, 'Base restaurada (%s tablas%s)'
                          % (tablas_nueva, ', %s errores' % len(errores) if errores else ''))
        else:
            tarea.paso_ok(indice, 'Base restaurada')

    # 7 -------------------------------------------------------- credenciales
    # USE_SSL queda en false: sólo pasa a true cuando el certificado ya funciona.
    indice = tarea.paso('Configurar credenciales.json')
    archivo_credenciales = os.path.join(destino, 'credenciales.json')
    valor_dominio = dominio or '(sin cambio)'
    if not simular:
        nuevas = _actualizar_credenciales(
            archivo_credenciales,
            cambios={'POSTGRES_DBNAME': base, 'DEBUG': bool(datos.get('debug', False))},
            dominio=dominio, ssl=False)
        valor_dominio = nuevas.get('DOMINIO_GENERAL') if dominio else valor_dominio
    tarea.log('  POSTGRES_DBNAME=%s · DOMINIO_GENERAL=%s · USE_SSL=False' % (base, valor_dominio))
    tarea.paso_ok(indice)

    # 8 ------------------------------------------------------------ migrate
    indice = tarea.paso('Sincronizar migraciones (migrate --fake)')
    tarea.ejecutar([python, 'manage.py', 'makemigrations', '--noinput'],
                   cwd=destino, simular=simular)
    tarea.ejecutar([python, 'manage.py', 'migrate', '--fake', '--noinput'],
                   cwd=destino, simular=simular)
    tarea.paso_ok(indice)

    # 9 ------------------------------------------------------- systemd + socket
    if con_servicio:
        indice = tarea.paso('Crear el socket y el servicio systemd «%s»' % cliente)
        archivo_unidad = os.path.join(DIR_UNIDADES, '%s.service' % cliente)
        archivo_socket = os.path.join(DIR_UNIDADES, '%s.socket' % cliente)
        texto_unidad = texto_servicio(cliente, destino, venv, proyecto)
        contenido_socket = texto_socket(cliente)
        tarea.log('--- %s ---\n%s' % (archivo_socket, contenido_socket))
        tarea.log('--- %s ---\n%s' % (archivo_unidad, texto_unidad))
        if not simular:
            if not unidad_existia:
                tarea.registrar_deshacer('unidad', cliente)
                registro_actualizar(config, cliente, unidad=cliente, socket=cliente)
            with open(archivo_socket, 'w', encoding='utf-8') as fh:
                fh.write(contenido_socket)
            with open(archivo_unidad, 'w', encoding='utf-8') as fh:
                fh.write(texto_unidad)
        tarea.ejecutar(['systemctl', 'daemon-reload'], simular=simular)
        tarea.ejecutar(['systemctl', 'enable', '--now', '%s.socket' % cliente], simular=simular)
        tarea.ejecutar(['systemctl', 'enable', '%s.service' % cliente], simular=simular)
        # restart (no «start») para que tome el credenciales.json nuevo si ya corría.
        tarea.ejecutar(['systemctl', 'restart', '%s.service' % cliente], simular=simular)
        tarea.paso_ok(indice, archivo_unidad)

    # 10 ------------------------------------------------------------- apache
    ssl_activo = False
    if datos.get('crear_vhost', True) and dominio:
        indice = tarea.paso('Crear el vhost de Apache para %s' % dominio)
        if not simular and not vhost_existia:
            tarea.registrar_deshacer('vhost', dominio)
            tarea.registrar_deshacer('log', cliente)
            registro_actualizar(config, cliente, sitio=dominio)
        # Lo de un intento anterior (otro dominio, -le-ssl) se aparta: debe quedar un solo .conf.
        for viejo in previo['vhosts'] if reanudar else []:
            if viejo.get('sitio') != dominio:
                tarea.log('  Se desactiva %s, de un intento anterior' % viejo['archivo'], 'aviso')
                tarea.ejecutar(['a2dissite', viejo['sitio']], critico=False, simular=simular)
                respaldo = '%s.bak-%s' % (viejo['archivo'], tarea.id)
                tarea.log('$ mv %s %s%s' % (viejo['archivo'], respaldo,
                                            '   [simulación, no se ejecuta]' if simular else ''), 'cmd')
                if not simular and os.path.isfile(viejo['archivo']):
                    os.rename(viejo['archivo'], respaldo)
        if not _escribir_vhost_activo(tarea, dominio, texto_vhost(cliente, dominio, destino),
                                      simular=simular):
            tarea.paso_error(indice, 'Apache rechazó el vhost')
            raise TareaError('apache2ctl configtest falló: revisa la salida de arriba')
        tarea.paso_ok(indice, _archivo_vhost(dominio))

        # 11 -------------------------------------------------------- certificado
        if datos.get('certbot'):
            ssl_activo = _configurar_ssl(tarea, config, cliente, dominio, destino,
                                         con_servicio=con_servicio, simular=simular)
            if not ssl_activo:
                tarea.datos['ssl_pendiente'] = True
                registro_actualizar(config, cliente, ssl_pendiente=True)
                tarea.log('  La instancia queda en HTTP con USE_SSL=false. Cuando el DNS apunte '
                          'aquí, usa «Reintentar SSL».', 'aviso')
            else:
                registro_actualizar(config, cliente, ssl_pendiente=False)

    # 12 ----------------------------------------------------------- verificar
    indice = tarea.paso('Verificar la instancia')
    if simular:
        tarea.paso_ok(indice, 'Simulación: no se verifica nada real')
    else:
        fallos = verificar_instancia(tarea, cliente, dominio, con_servicio=con_servicio)
        if datos.get('certbot') and dominio and not ssl_activo and datos.get('crear_vhost', True):
            fallos.append('sin certificado: el sitio queda en HTTP')
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
            # ser de configuración. Se corrige con Reanudar o se quita con Eliminar.
            tarea.paso_error(indice, 'Creada, pero con problemas: %s' % '; '.join(fallos))
            tarea.datos['problemas'] = fallos
        else:
            tarea.paso_ok(indice, 'Instancia funcionando')
        tarea.datos['instancia_id'] = '%s|%s' % (cliente, tipo)

    tarea.log('Recuerda revisar el resto de credenciales.json (SMTP, tokens) '
              'y los datos de la empresa en el sistema.', 'aviso')


def reintentar_ssl(tarea, config, colector):
    """Vuelve a intentar sólo el certificado de una instancia creada por el panel."""
    cliente = tarea.datos.get('cliente')
    entrada = registro_obtener(config, cliente)
    if not entrada or not entrada.get('dominio') or not entrada.get('sitio'):
        raise TareaError('Esa instancia no tiene un vhost creado por el panel')
    destino = os.path.join(entrada['carpeta'], entrada['proyecto'])
    if not _configurar_ssl(tarea, config, cliente, entrada['dominio'], destino,
                           con_servicio=bool(entrada.get('unidad'))):
        raise TareaError('No se pudo activar HTTPS: el sitio sigue en HTTP')
    registro_actualizar(config, cliente, ssl_pendiente=False)
    colector.refrescar(forzar=True)


# ----------------------------------------------------------------- verificar
def verificar_instancia(tarea, cliente, dominio, con_servicio=True, espera=30):
    """Comprueba que la instancia recién creada realmente arrancó y responde.

    Devuelve la lista de fallos (vacía si todo está bien).
    """
    fallos = []
    if not con_servicio:
        tarea.log('  No se creó el servicio: no se prueba si la aplicación responde', 'aviso')
        return fallos

    ruta = _ruta_socket(cliente)
    limite = time.time() + espera
    escucha = False
    while time.time() < limite:
        if _socket_responde(ruta):
            escucha = True
            break
        time.sleep(1)
    tarea.log('  %s Socket %s %s' % ('✔' if escucha else '✖', ruta,
                                     'escuchando' if escucha else 'sin respuesta'),
              'ok' if escucha else 'error')
    if not escucha:
        fallos.append('el socket no responde (revisa: journalctl -u %s.socket -n 50)' % cliente)
        return fallos

    # La primera petición también arranca gunicorn si el socket estaba esperando.
    codigo = None
    try:
        conexion = _ConexionUnix(ruta, timeout=60)
        conexion.request('GET', '/', headers={'Host': dominio or 'localhost',
                                              'User-Agent': 'integrasolucadminvps'})
        codigo = conexion.getresponse().status
        conexion.close()
    except Exception as ex:
        tarea.log('  ✖ La aplicación no respondió: %s' % ex, 'error')
    if codigo is None:
        fallos.append('la aplicación no responde (revisa: journalctl -u %s -n 50)' % cliente)
        return fallos
    bien = codigo < 500
    tarea.log('  %s La aplicación responde por el socket: HTTP %s' % ('✔' if bien else '✖', codigo),
              'ok' if bien else 'error')
    if not bien:
        fallos.append('la aplicación devuelve HTTP %s (revisa DEBUG/ALLOWED_HOSTS y el log)' % codigo)
    return fallos


# ------------------------------------------- piezas comunes de Deshacer y Eliminar
def _cerrar_conexiones(tarea, credenciales, base, simular=False):
    tarea.ejecutar(['psql'] + _psql_args(credenciales) + [
        '-c', "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
              "WHERE datname = '%s' AND pid <> pg_backend_pid();" % base, 'postgres'],
        entorno=_entorno_pg(credenciales), critico=False, simular=simular,
        ocultar=[credenciales.get('POSTGRES_PASSWORD')])


def _quitar_vhost(tarea, sitio):
    """Desactiva el sitio y borra su archivo (y respaldos). No recarga Apache."""
    tarea.ejecutar(['a2dissite', sitio], critico=False)
    archivos = [_archivo_vhost(sitio)] + glob.glob('%s.bak-*' % _archivo_vhost(sitio))
    enlace = os.path.join(DIR_SITIOS_ACTIVOS, '%s.conf' % sitio)
    if os.path.lexists(enlace):
        archivos.append(enlace)
    for archivo in archivos:
        if os.path.lexists(archivo):
            os.remove(archivo)
            tarea.log('  Eliminado %s' % archivo)


def _quitar_respaldos_vhost(tarea, carpeta):
    """Los .bak-<tarea> que dejó Reanudar con otro nombre de sitio, si son de esta instancia."""
    for archivo in glob.glob(os.path.join(DIR_SITIOS_DISPONIBLES, '*.conf.bak-*')):
        try:
            with open(archivo, 'r', encoding='utf-8', errors='replace') as fh:
                if carpeta.rstrip('/') + '/' in fh.read():
                    os.remove(archivo)
                    tarea.log('  Eliminado %s' % archivo)
        except OSError as ex:
            tarea.log('  No se pudo borrar %s: %s' % (archivo, ex), 'aviso')


def _quitar_logs(tarea, cliente):
    for tipo_log in ('error', 'access'):
        for archivo in glob.glob(os.path.join(DIR_LOGS_APACHE, '%s_%s.log*' % (cliente, tipo_log))):
            try:
                os.remove(archivo)
                tarea.log('  Eliminado %s' % archivo)
            except OSError as ex:
                tarea.log('  No se pudo borrar %s: %s' % (archivo, ex), 'aviso')


def _quitar_servicio(tarea, cliente):
    """Primero el socket: si no, la siguiente petición volvería a levantar el servicio."""
    for unidad in ('%s.socket' % cliente, '%s.service' % cliente):
        archivo = os.path.join(DIR_UNIDADES, unidad)
        if os.path.isfile(archivo):
            tarea.ejecutar(['systemctl', 'disable', '--now', unidad], critico=False)
            os.remove(archivo)
            tarea.log('  Eliminado %s' % archivo)
    tarea.ejecutar(['systemctl', 'daemon-reload'], critico=False)
    tarea.ejecutar(['systemctl', 'reset-failed', '%s.service' % cliente], critico=False)
    ruta = _ruta_socket(cliente)
    if os.path.exists(ruta):
        os.remove(ruta)


def _borrar_base(tarea, credenciales, base):
    _cerrar_conexiones(tarea, credenciales, base)
    codigo, _ = tarea.ejecutar(['psql'] + _psql_args(credenciales) +
                               ['-c', 'DROP DATABASE IF EXISTS %s;' % base, 'postgres'],
                               entorno=_entorno_pg(credenciales), critico=False,
                               ocultar=[credenciales.get('POSTGRES_PASSWORD')])
    return codigo == 0


def _carpeta_permitida(config, carpeta):
    """Sólo <base_dir>/<cliente>, sin enlaces que apunten a otro lado y nunca un template."""
    if not carpeta:
        return False
    real = os.path.realpath(carpeta)
    base = os.path.realpath(_base_dir(config))
    if os.path.dirname(real) != base or real == base:
        return False
    for plantilla in ((config.get('aprovisionamiento') or {}).get('templates') or {}).values():
        ruta = os.path.realpath(plantilla.get('ruta') or '')
        if plantilla.get('ruta') and (ruta == real or ruta.startswith(real + os.sep)):
            return False
    return True


def _borrar_carpeta(tarea, config, carpeta):
    if not _carpeta_permitida(config, carpeta):
        tarea.log('  Ruta no permitida para borrado: %s' % carpeta, 'error')
        return False
    shutil.rmtree(carpeta, ignore_errors=True)
    if os.path.exists(carpeta):
        tarea.log('  La carpeta %s sigue existiendo' % carpeta, 'error')
        return False
    tarea.log('  Eliminada %s' % carpeta)
    return True


def _bases_protegidas(config, cliente):
    """Bases de los templates y de todas las demás instancias, aunque estén ocultas."""
    protegidas = {}
    cfg = config.get('aprovisionamiento') or {}
    for tipo, plantilla in (cfg.get('templates') or {}).items():
        if plantilla.get('db_origen'):
            protegidas[plantilla['db_origen']] = 'base origen del template %s' % tipo
    proyectos = list((config.get('proyectos') or {}).values())
    for carpeta_base in (config.get('base_dirs') or [_base_dir(config)]):
        for proyecto in proyectos:
            for archivo in glob.glob(os.path.join(carpeta_base, '*', proyecto, 'credenciales.json')):
                dueno = os.path.basename(os.path.dirname(os.path.dirname(archivo)))
                if dueno == cliente:
                    continue
                try:
                    with open(archivo, 'r', encoding='utf-8') as fh:
                        nombre = (json.load(fh) or {}).get('POSTGRES_DBNAME')
                except (OSError, ValueError):
                    continue
                if nombre:
                    protegidas[nombre] = 'la usa la instancia %s' % dueno
    return protegidas


def _certificado_ajeno(config, nombre, cliente, carpeta):
    """Vhost de otra instancia que usa ese certificado, si hay alguno."""
    marca = '/live/%s/' % nombre
    for vhost in webserver.cargar_vhosts(config):
        if marca in (vhost.get('certificado') or '') and not _vhost_propio(vhost, cliente, carpeta):
            return vhost.get('nombre') or vhost.get('archivo') or '?'
    return None


# -------------------------------------------------------------------- eliminar
def plan_eliminacion(config, cliente):
    """Qué se borraría y qué se conserva, sin tocar nada. None si no es del panel."""
    entrada = registro_obtener(config, cliente)
    if not entrada:
        return None
    borrar, conservar = [], []
    carpeta = entrada.get('carpeta') or ''

    def anotar(lista, recurso, valor, detalle=None):
        lista.append({'recurso': recurso, 'valor': valor, 'detalle': detalle})

    carpeta_ok = _carpeta_permitida(config, carpeta)
    if not carpeta_ok:
        anotar(conservar, 'carpeta', carpeta, 'ruta no permitida para borrado')
    elif os.path.exists(carpeta):
        anotar(borrar, 'carpeta', carpeta, 'incluye media/')

    base = entrada.get('base')
    if base:
        protegidas = _bases_protegidas(config, cliente)
        en_credenciales = None
        try:
            archivo = os.path.join(carpeta, entrada.get('proyecto') or '', 'credenciales.json')
            with open(archivo, 'r', encoding='utf-8') as fh:
                en_credenciales = (json.load(fh) or {}).get('POSTGRES_DBNAME')
        except (OSError, ValueError):
            pass
        if not RE_DB.match(base):
            anotar(conservar, 'base', base, 'nombre no válido')
        elif base in protegidas:
            anotar(conservar, 'base', base, protegidas[base])
        elif en_credenciales and en_credenciales != base:
            anotar(conservar, 'base', base,
                   'credenciales.json apunta a otra base (%s)' % en_credenciales)
        elif _existe_base(config, entrada.get('tipo'), base) is not False:
            anotar(borrar, 'base', base)

    if entrada.get('unidad'):
        unidad = next((u for u in units.cargar_unidades(config)
                       if u['unidad'] == entrada['unidad']), None)
        propia = bool(unidad and _es_propia({'carpeta': carpeta}, unidad.get('workingdirectory')))
        if unidad and not propia:
            anotar(conservar, 'servicio', entrada['unidad'],
                   'su WorkingDirectory es %s' % unidad.get('workingdirectory'))
        elif unidad:
            anotar(borrar, 'servicio', entrada['unidad'])
        socket_unidad = units.cargar_sockets(config).get(entrada['unidad'])
        if socket_unidad:
            if (not unidad or propia) and socket_unidad.get('rutas') == [_ruta_socket(cliente)]:
                anotar(borrar, 'socket', entrada['unidad'])
            else:
                anotar(conservar, 'socket', entrada['unidad'], 'no es el socket de esta instancia')

    dominio = (entrada.get('dominio') or '').lower()
    for vhost in webserver.cargar_vhosts(config):
        if vhost.get('servidor') != 'apache' or not _vhost_propio(vhost, cliente, carpeta):
            continue
        if (vhost.get('servername') or '').lower() == dominio:
            anotar(borrar, 'vhost', vhost['sitio'], vhost['archivo'])
        else:
            anotar(conservar, 'vhost', vhost['sitio'],
                   'su dominio es %s, no %s' % (vhost.get('servername'), dominio))

    certificado = entrada.get('certificado')
    if certificado:
        ajeno = _certificado_ajeno(config, certificado, cliente, carpeta)
        if not RE_DOMINIO.match(certificado):
            anotar(conservar, 'certificado', certificado, 'nombre no válido')
        elif ajeno:
            anotar(conservar, 'certificado', certificado, 'lo usa %s' % ajeno)
        elif os.path.isdir(os.path.join(certificados.RUTA_LIVE, certificado)):
            anotar(borrar, 'certificado', certificado)

    return {'cliente': cliente, 'tipo': entrada.get('tipo'), 'dominio': entrada.get('dominio'),
            'base': base, 'borrar': borrar, 'conservar': conservar,
            'con_base': any(i['recurso'] == 'base' for i in borrar)}


def _restantes(config, entrada, plan):
    """Lo del plan que sigue existiendo después de borrar."""
    quedan = []
    for item in plan['borrar']:
        recurso, valor = item['recurso'], item['valor']
        if recurso == 'carpeta':
            existe = os.path.exists(valor)
        elif recurso == 'base':
            existe = _existe_base(config, entrada.get('tipo'), valor) is not False
        elif recurso == 'servicio':
            existe = os.path.exists(os.path.join(DIR_UNIDADES, '%s.service' % valor))
        elif recurso == 'socket':
            existe = os.path.exists(os.path.join(DIR_UNIDADES, '%s.socket' % valor))
        elif recurso == 'vhost':
            existe = (os.path.exists(item.get('detalle') or '')
                      or os.path.lexists(os.path.join(DIR_SITIOS_ACTIVOS, '%s.conf' % valor)))
        else:
            existe = os.path.isdir(os.path.join(certificados.RUTA_LIVE, valor))
        if existe:
            quedan.append('%s %s' % (recurso, valor))
    return quedan


def eliminar_instancia(tarea, config, colector, cliente, con_backup=True):
    """Borra lo que el panel creó para la instancia y comprueba que no quedó nada."""
    plan = plan_eliminacion(config, cliente)
    entrada = registro_obtener(config, cliente)
    if not plan:
        raise TareaError('«%s» no fue creada desde el panel: no se puede eliminar aquí' % cliente)
    for item in plan['conservar']:
        tarea.log('  Se conserva %s %s: %s' % (item['recurso'], item['valor'], item['detalle']), 'aviso')
    de_tipo = lambda recurso: [i for i in plan['borrar'] if i['recurso'] == recurso]
    credenciales = _credenciales_template(config, entrada.get('tipo')) if plan['con_base'] else None

    # La copia va primero: si falla no se ha tocado nada.
    if plan['con_base']:
        if con_backup:
            indice = tarea.paso('Copia de seguridad de la base %s' % plan['base'])
            resultado = mod_backups.respaldar_base(tarea, config, credenciales, plan['base'],
                                                   cliente, indice)
            if not resultado.get('ok'):
                raise TareaError('No se pudo hacer la copia (%s): no se borró nada'
                                 % resultado.get('error'))
            tarea.datos['backup'] = resultado.get('archivo')
        else:
            tarea.log('Se omite la copia de la base por pedido del usuario', 'aviso')

    # El vhost antes que el certificado: Apache no arranca si falta un certificado que usa.
    if de_tipo('vhost'):
        indice = tarea.paso('Quitar el vhost')
        for item in de_tipo('vhost'):
            _quitar_vhost(tarea, item['valor'])
        _quitar_respaldos_vhost(tarea, entrada.get('carpeta') or '')
        _quitar_logs(tarea, cliente)
        if not _recargar_apache(tarea):
            tarea.paso_error(indice, 'Apache no recargó')
            raise TareaError('Apache no aceptó la configuración: se detiene sin borrar nada más')
        tarea.paso_ok(indice)

    for item in de_tipo('certificado'):
        indice = tarea.paso('Eliminar el certificado %s' % item['valor'])
        resultado = certificados.eliminar(item['valor'], config, forzar=True)
        if resultado.get('ok'):
            tarea.paso_ok(indice)
        else:
            tarea.paso_error(indice, resultado.get('error'))

    if de_tipo('servicio') or de_tipo('socket'):
        indice = tarea.paso('Quitar el socket y el servicio')
        _quitar_servicio(tarea, cliente)
        tarea.paso_ok(indice)

    if plan['con_base']:
        indice = tarea.paso('Eliminar la base %s' % plan['base'])
        if _borrar_base(tarea, credenciales, plan['base']):
            tarea.paso_ok(indice)
        else:
            tarea.paso_error(indice, 'No se pudo borrar la base')

    for item in de_tipo('carpeta'):
        indice = tarea.paso('Eliminar la carpeta %s' % item['valor'])
        if _borrar_carpeta(tarea, config, item['valor']):
            tarea.paso_ok(indice)
        else:
            tarea.paso_error(indice, 'La carpeta no se pudo borrar')

    indice = tarea.paso('Comprobar que no quedó nada')
    quedan = _restantes(config, entrada, plan) + ['%s %s' % (i['recurso'], i['valor'])
                                                  for i in plan['conservar']]
    colector.refrescar(forzar=True)
    if quedan:
        tarea.paso_error(indice, 'Sigue existiendo: %s' % ', '.join(quedan))
        raise TareaError('Eliminación incompleta (%s). Sigue en la lista para reintentar.'
                         % ', '.join(quedan))
    registro_quitar(config, cliente)
    tarea.paso_ok(indice, 'Instancia eliminada')


# -------------------------------------------------------------------- deshacer
ORDEN_DESHACER = ('vhost', 'log', 'certificado', 'unidad', 'base', 'carpeta')


def deshacer(tarea, config, colector, tarea_destino):
    """Revierte lo que creó una tarea (vhost, certificado, servicio, base, carpeta)."""
    acciones = sorted(tarea_destino.deshacer or [],
                      key=lambda a: ORDEN_DESHACER.index(a['tipo'])
                      if a['tipo'] in ORDEN_DESHACER else len(ORDEN_DESHACER))
    if not acciones:
        tarea.log('La tarea no registró nada que deshacer', 'aviso')
        return

    datos_destino = tarea_destino.datos or {}
    tipo_sistema = datos_destino.get('tipo')
    cliente = datos_destino.get('cliente')
    carpeta = os.path.join(_base_dir(config), cliente or '')
    fallos = []
    vhost_quitado = False
    for accion in acciones:
        valor = accion['valor']
        if accion['tipo'] == 'vhost':
            indice = tarea.paso('Quitar vhost %s' % valor)
            _quitar_vhost(tarea, valor)
            vhost_quitado = True
            tarea.paso_ok(indice)
        elif accion['tipo'] == 'log':
            _quitar_logs(tarea, valor)
        elif accion['tipo'] == 'certificado':
            if vhost_quitado:
                _recargar_apache(tarea)
                vhost_quitado = False
            indice = tarea.paso('Eliminar el certificado %s' % valor)
            ajeno = _certificado_ajeno(config, valor, cliente or '', carpeta)
            if ajeno or not RE_DOMINIO.match(valor):
                tarea.paso_error(indice, 'Se conserva: %s' % (ajeno or 'nombre no válido'))
                fallos.append('certificado %s' % valor)
                continue
            resultado = certificados.eliminar(valor, config, forzar=True)
            if resultado.get('ok'):
                tarea.paso_ok(indice)
            else:
                tarea.paso_error(indice, resultado.get('error'))
                fallos.append('certificado %s' % valor)
        elif accion['tipo'] == 'unidad':
            if vhost_quitado:
                _recargar_apache(tarea)
                vhost_quitado = False
            indice = tarea.paso('Quitar servicio %s' % valor)
            _quitar_servicio(tarea, valor)
            tarea.paso_ok(indice)
        elif accion['tipo'] == 'base':
            indice = tarea.paso('Eliminar la base %s' % valor)
            protegida = _bases_protegidas(config, cliente).get(valor)
            if protegida:
                tarea.paso_error(indice, 'Se conserva: %s' % protegida)
                fallos.append('base %s' % valor)
                continue
            credenciales = _credenciales_template(config, tipo_sistema)
            if _borrar_base(tarea, credenciales, valor):
                tarea.paso_ok(indice)
            else:
                tarea.paso_error(indice, 'No se pudo borrar la base')
                fallos.append('base %s' % valor)
        elif accion['tipo'] == 'carpeta':
            indice = tarea.paso('Eliminar la carpeta %s' % valor)
            if _borrar_carpeta(tarea, config, valor):
                tarea.paso_ok(indice)
            else:
                tarea.paso_error(indice, 'La carpeta no se pudo borrar')
                fallos.append('carpeta %s' % valor)
    if vhost_quitado:
        _recargar_apache(tarea)

    colector.refrescar(forzar=True)
    if fallos:
        raise TareaError('Reversión incompleta: %s' % ', '.join(fallos))
    tarea_destino.datos['deshecha'] = True
    tarea_destino._persistir()
    entrada = registro_obtener(config, cliente) if cliente else None
    if entrada and entrada.get('tarea') == tarea_destino.id:
        registro_quitar(config, cliente)
    tarea.log('Reversión terminada', 'ok')
