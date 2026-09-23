# -*- coding: utf-8 -*-
"""Qué consume cada cosa en el servidor: servicios, procesos, discos y carpetas.

Todo se mide en el momento. La CPU se calcula como en `top`: se toman dos
lecturas separadas por un segundo y se compara el tiempo de CPU consumido
(por unidad de systemd con CPUUsageNSec y por proceso con /proc/<pid>/stat).
"""
from __future__ import annotations

import os
import threading
import time

from .systemd import recursos_del_sistema
from .utils import bytes_legible, duracion_legible, ejecutar

INTERVALO = 1.0
PROPIEDADES = 'Id,Description,MemoryCurrent,CPUUsageNSec,TasksCurrent,MainPID,ActiveEnterTimestamp'
SISTEMAS_ARCHIVOS = ('ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'f2fs', 'reiserfs', 'jfs')

# Servicios que no son instancias pero que las sostienen.
INFRAESTRUCTURA = ('postgresql', 'apache2', 'httpd', 'nginx', 'redis', 'mysql', 'mariadb',
                   'memcached', 'integrasolucadmin', 'docker', 'containerd', 'supervisor',
                   'cron', 'ssh', 'fail2ban', 'ufw', 'systemd-journald', 'rabbitmq', 'celery')

_LOCK_CARPETAS = threading.Lock()
_CACHE_CARPETAS = {'momento': 0, 'datos': None}


# ------------------------------------------------------------------ servicios
def _leer_unidades(nombres):
    """systemctl show de varias unidades a la vez: {unidad: {propiedad: valor}}."""
    if not nombres:
        return {}
    codigo, salida, _ = ejecutar(['systemctl', 'show', '--no-pager', '--property=' + PROPIEDADES]
                                 + list(nombres), timeout=30)
    if codigo != 0 and not salida:
        return {}
    unidades = {}
    for bloque in (salida or '').split('\n\n'):
        valores = dict(l.split('=', 1) for l in bloque.splitlines() if '=' in l)
        if valores.get('Id'):
            unidades[valores['Id']] = valores
    return unidades


def _entero(valor):
    valor = (valor or '').strip()
    return int(valor) if valor.isdigit() else None


def _categoria(unidad, por_servicio):
    base = unidad[:-len('.service')] if unidad.endswith('.service') else unidad
    if base in por_servicio:
        return 'instancia'
    if any(base == n or base.startswith(n + '@') or base.startswith(n + '-') for n in INFRAESTRUCTURA):
        return 'infraestructura'
    return 'sistema'


def servicios(instancias=None):
    """Servicios en ejecución con su RAM, % de CPU (medido en 1 s) y tareas."""
    codigo, salida, error = ejecutar(
        ['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend',
         '--plain', '--no-pager'], timeout=20)
    if codigo != 0:
        return {'ok': False, 'error': error or 'systemctl no disponible', 'servicios': []}
    nombres = [l.split()[0] for l in (salida or '').splitlines() if l.strip()]

    por_servicio = {}
    for inst in (instancias or []):
        if inst.get('servicio'):
            por_servicio[inst['servicio']] = inst

    t0 = time.time()
    primera = _leer_unidades(nombres)
    time.sleep(INTERVALO)
    segunda = _leer_unidades(nombres)
    transcurrido = max(time.time() - t0, 0.001)
    nucleos = os.cpu_count() or 1

    filas = []
    for unidad in nombres:
        datos = segunda.get(unidad) or primera.get(unidad) or {}
        memoria = _entero(datos.get('MemoryCurrent'))
        cpu_a = _entero((primera.get(unidad) or {}).get('CPUUsageNSec'))
        cpu_b = _entero((segunda.get(unidad) or {}).get('CPUUsageNSec'))
        cpu_pct = None
        if cpu_a is not None and cpu_b is not None and cpu_b >= cpu_a:
            cpu_pct = round((cpu_b - cpu_a) / (transcurrido * 1e9) * 100.0, 1)
        base = unidad[:-len('.service')] if unidad.endswith('.service') else unidad
        inst = por_servicio.get(base) or {}
        filas.append({
            'unidad': unidad,
            'nombre': base,
            'descripcion': datos.get('Description'),
            'categoria': _categoria(unidad, por_servicio),
            'cliente': inst.get('cliente'),
            'instancia': inst.get('id'),
            'tipo': inst.get('tipo'),
            'memoria_bytes': memoria,
            'memoria': bytes_legible(memoria) if memoria is not None else '-',
            'cpu_pct': cpu_pct,
            # % sobre la capacidad total del servidor (todos los núcleos)
            'cpu_pct_total': round(cpu_pct / nucleos, 1) if cpu_pct is not None else None,
            'tareas': _entero(datos.get('TasksCurrent')),
            'pid': _entero(datos.get('MainPID')),
            'desde': datos.get('ActiveEnterTimestamp') or None,
        })
    filas.sort(key=lambda f: -(f['memoria_bytes'] or 0))

    totales = {}
    for fila in filas:
        grupo = totales.setdefault(fila['categoria'], {'servicios': 0, 'memoria_bytes': 0,
                                                       'cpu_pct': 0.0})
        grupo['servicios'] += 1
        grupo['memoria_bytes'] += fila['memoria_bytes'] or 0
        grupo['cpu_pct'] += fila['cpu_pct'] or 0.0
    for grupo in totales.values():
        grupo['memoria'] = bytes_legible(grupo['memoria_bytes'])
        grupo['cpu_pct'] = round(grupo['cpu_pct'], 1)
    return {'ok': True, 'servicios': filas, 'totales': totales, 'nucleos': nucleos}


# ------------------------------------------------------------------- procesos
def _tics_de(pid):
    try:
        with open('/proc/%s/stat' % pid, 'r', encoding='utf-8', errors='replace') as fh:
            texto = fh.read()
    except OSError:
        return None
    # El nombre va entre paréntesis y puede tener espacios: se corta tras el último ')'.
    campos = texto[texto.rfind(')') + 2:].split()
    try:
        return int(campos[11]) + int(campos[12])      # utime + stime
    except (IndexError, ValueError):
        return None


def _usuarios():
    nombres = {}
    try:
        with open('/etc/passwd', 'r', encoding='utf-8', errors='replace') as fh:
            for linea in fh:
                partes = linea.split(':')
                if len(partes) > 2:
                    nombres[partes[2]] = partes[0]
    except OSError:
        pass
    return nombres


def _info_proceso(pid, usuarios, pagina):
    datos = {'pid': int(pid)}
    try:
        with open('/proc/%s/status' % pid, 'r', encoding='utf-8', errors='replace') as fh:
            for linea in fh:
                if linea.startswith('Name:'):
                    datos['nombre'] = linea.split(None, 1)[1].strip()
                elif linea.startswith('Uid:'):
                    datos['usuario'] = usuarios.get(linea.split()[1], linea.split()[1])
        with open('/proc/%s/statm' % pid, 'r', encoding='utf-8') as fh:
            datos['rss_bytes'] = int(fh.read().split()[1]) * pagina
        with open('/proc/%s/cmdline' % pid, 'rb') as fh:
            comando = fh.read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()
        datos['comando'] = comando[:300] or '[%s]' % datos.get('nombre', '')
    except (OSError, ValueError, IndexError):
        return None
    try:
        datos['cwd'] = os.readlink('/proc/%s/cwd' % pid)
    except OSError:
        datos['cwd'] = None
    return datos


def procesos(limite=25, instancias=None):
    """Procesos que más RAM y CPU usan, con la instancia a la que pertenecen."""
    if not os.path.isdir('/proc'):
        return {'ok': False, 'error': '/proc no disponible', 'por_memoria': [], 'por_cpu': []}
    hz = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
    pagina = os.sysconf('SC_PAGE_SIZE') if hasattr(os, 'sysconf') else 4096
    ram_total = recursos_del_sistema().get('ram_total') or 0

    pids = [p for p in os.listdir('/proc') if p.isdigit()]
    antes = {p: _tics_de(p) for p in pids}
    t0 = time.time()
    time.sleep(INTERVALO)
    transcurrido = max(time.time() - t0, 0.001)

    rutas = sorted((((i.get('ruta') or '').rstrip('/'), i) for i in (instancias or [])
                    if i.get('ruta')), key=lambda par: -len(par[0]))
    usuarios = _usuarios()
    filas = []
    for pid in pids:
        despues = _tics_de(pid)
        if despues is None:
            continue
        datos = _info_proceso(pid, usuarios, pagina)
        if not datos:
            continue
        previo = antes.get(pid)
        datos['cpu_pct'] = (round((despues - previo) / hz / transcurrido * 100.0, 1)
                            if previo is not None and despues >= previo else 0.0)
        datos['rss'] = bytes_legible(datos['rss_bytes'])
        datos['ram_pct'] = round(datos['rss_bytes'] * 100.0 / ram_total, 1) if ram_total else None
        # A qué instancia pertenece: por su carpeta de trabajo o por la ruta
        # que aparece en la línea de comandos (gunicorn, manage.py...).
        cwd = datos.get('cwd') or ''
        comando = ' %s ' % datos['comando']
        for ruta, inst in rutas:
            if not ruta:
                continue
            if (cwd == ruta or cwd.startswith(ruta + '/') or (ruta + '/') in comando
                    or (ruta + ' ') in comando):
                datos['cliente'] = inst.get('cliente')
                datos['instancia'] = inst.get('id')
                break
        datos.pop('cwd', None)
        filas.append(datos)

    por_memoria = sorted(filas, key=lambda f: -f['rss_bytes'])[:limite]
    por_cpu = sorted([f for f in filas if f['cpu_pct'] > 0], key=lambda f: -f['cpu_pct'])[:limite]
    return {'ok': True, 'total': len(filas), 'por_memoria': por_memoria, 'por_cpu': por_cpu}


# -------------------------------------------------------------------- discos
def discos():
    """Uso de cada sistema de archivos real montado (sin tmpfs, overlay, etc.)."""
    vistos, filas = set(), []
    try:
        with open('/proc/mounts', 'r', encoding='utf-8') as fh:
            montajes = [l.split() for l in fh if l.strip()]
    except OSError:
        montajes = [['/', '/', 'ext4']]
    for partes in montajes:
        if len(partes) < 3:
            continue
        dispositivo, punto, tipo = partes[0], partes[1], partes[2]
        if tipo not in SISTEMAS_ARCHIVOS or dispositivo in vistos:
            continue
        vistos.add(dispositivo)
        try:
            st = os.statvfs(punto)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if not total:
            continue
        libre = st.f_bavail * st.f_frsize
        usado = total - st.f_bfree * st.f_frsize
        inodos = (round((st.f_files - st.f_ffree) * 100.0 / st.f_files, 1)
                  if st.f_files else None)
        filas.append({'dispositivo': dispositivo, 'montaje': punto, 'tipo': tipo,
                      'total': bytes_legible(total), 'usado': bytes_legible(usado),
                      'libre': bytes_legible(libre), 'total_bytes': total,
                      'usado_bytes': usado, 'libre_bytes': libre,
                      'porcentaje': round(usado * 100.0 / total, 1), 'inodos_pct': inodos})
    filas.sort(key=lambda f: f['montaje'])
    return filas


def _swap():
    datos = {}
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as fh:
            for linea in fh:
                clave, _, valor = linea.partition(':')
                if clave in ('SwapTotal', 'SwapFree', 'Cached', 'Buffers'):
                    datos[clave] = int(valor.split()[0]) * 1024
    except (OSError, ValueError):
        return {}
    total = datos.get('SwapTotal') or 0
    usado = total - (datos.get('SwapFree') or 0)
    return {'total': bytes_legible(total), 'usado': bytes_legible(usado),
            'porcentaje': round(usado * 100.0 / total, 1) if total else None,
            'cache': bytes_legible((datos.get('Cached') or 0) + (datos.get('Buffers') or 0))}


def _uptime():
    try:
        with open('/proc/uptime', 'r', encoding='utf-8') as fh:
            return duracion_legible(float(fh.read().split()[0]))
    except (OSError, ValueError):
        return None


def sistema():
    datos = recursos_del_sistema()
    datos['swap'] = _swap()
    datos['uptime'] = _uptime()
    datos['discos'] = discos()
    try:
        datos['carga_1_5_15'] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        datos['carga_1_5_15'] = None
    return datos


# ------------------------------------------------------------------ carpetas
def _carpetas_de_interes(config):
    backups = (config.get('backups') or {}).get('destino') or '/home/backups'
    carpetas = [
        ('Backups del panel', backups),
        ('Datos de PostgreSQL', '/var/lib/postgresql'),
        ('Logs del sistema (/var/log)', '/var/log'),
        ('Journal de systemd', '/var/log/journal'),
        ('Logs de Apache', '/var/log/apache2'),
        ('Logs de nginx', '/var/log/nginx'),
        ('Temporales (/tmp)', '/tmp'),
        ('Caché de paquetes (apt)', '/var/cache/apt'),
        ("Certificados Let's Encrypt", '/etc/letsencrypt'),
    ]
    for repositorio in (config.get('backups') or {}).get('repositorios') or []:
        carpetas.append(('Repositorio de backups', repositorio))
    for plantilla in ((config.get('aprovisionamiento') or {}).get('templates') or {}).values():
        if plantilla.get('ruta'):
            carpetas.append(('Template', plantilla['ruta']))
    return carpetas


def carpetas(config, forzar=False, ttl=1800):
    """Tamaño de las carpetas que suelen llenar el disco (du, con caché de 30 min)."""
    with _LOCK_CARPETAS:
        if (not forzar and _CACHE_CARPETAS['datos'] is not None
                and time.time() - _CACHE_CARPETAS['momento'] < ttl):
            return dict(_CACHE_CARPETAS['datos'], cache=True)
        filas, vistos = [], set()
        timeout = int(config.get('timeout_du') or 120)
        for etiqueta, ruta in _carpetas_de_interes(config):
            real = os.path.realpath(ruta)
            if real in vistos or not os.path.isdir(real):
                continue
            vistos.add(real)
            codigo, salida, error = ejecutar(['du', '-sb', real], timeout=timeout)
            tamano = None
            if codigo == 0 and salida:
                try:
                    tamano = int(salida.split()[0])
                except (ValueError, IndexError):
                    tamano = None
            filas.append({'etiqueta': etiqueta, 'ruta': real, 'bytes': tamano,
                          'tamano': bytes_legible(tamano) if tamano is not None else '-',
                          'error': None if tamano is not None else (error or 'sin permiso')})
        filas.sort(key=lambda f: -(f['bytes'] or 0))
        datos = {'carpetas': filas,
                 'medido': time.strftime('%Y-%m-%d %H:%M:%S')}
        _CACHE_CARPETAS.update({'momento': time.time(), 'datos': datos})
        return dict(datos, cache=False)


# --------------------------------------------------------------- instancias
def por_instancia(instancias):
    """Resumen de lo que consume cada instancia (RAM, CPU, BD, media, logs)."""
    filas = []
    for inst in instancias or []:
        r = inst.get('resumen') or {}
        servicio = inst.get('servicio_estado') or {}
        filas.append({
            'id': inst.get('id'), 'cliente': inst.get('cliente'), 'tipo': inst.get('tipo'),
            'oculta': bool(inst.get('oculta')),
            'activo': bool(r.get('servicio_activo')),
            'ram_bytes': servicio.get('memoria_bytes') or 0,
            'ram': servicio.get('memoria') or '-',
            'cpu_pct': servicio.get('cpu_pct'),
            'db_bytes': r.get('db_tamano_bytes') or 0,
            'db': r.get('db_tamano') or '-',
            'media_bytes': r.get('media_bytes') or 0,
            'media': r.get('media_tamano') or '-',
            'logs_bytes': r.get('logs_bytes') or 0,
            'logs': r.get('logs_tamano') or '-',
            'disco_bytes': (r.get('db_tamano_bytes') or 0) + (r.get('media_bytes') or 0)
                           + (r.get('logs_bytes') or 0),
        })
    for fila in filas:
        fila['disco'] = bytes_legible(fila['disco_bytes'])
    filas.sort(key=lambda f: -f['ram_bytes'])
    return filas


def resumen(config, instancias):
    """Todo lo rápido (unos 2 s): sistema, servicios, procesos e instancias."""
    resultado = {'sistema': sistema(), 'instancias': por_instancia(instancias)}
    hilos = {}

    def medir(clave, funcion):
        try:
            resultado[clave] = funcion()
        except Exception as ex:   # pragma: no cover - defensivo
            resultado[clave] = {'ok': False, 'error': str(ex)}

    # Las dos mediciones esperan un segundo: se hacen en paralelo.
    for clave, funcion in (('servicios', lambda: servicios(instancias)),
                           ('procesos', lambda: procesos(instancias=instancias))):
        hilos[clave] = threading.Thread(target=medir, args=(clave, funcion), daemon=True)
        hilos[clave].start()
    for hilo in hilos.values():
        hilo.join(timeout=60)
    resultado['medido'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return resultado
