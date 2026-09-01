# -*- coding: utf-8 -*-
"""Apache, certificados SSL y verificación en vivo de la URL de cada instancia.

Todo es consulta en tiempo real: se leen los vhost de Apache, se inspecciona el
certificado con openssl y (opcionalmente) se golpea la URL pública.
"""
from __future__ import annotations

import datetime
import glob
import os
import re
import socket
import ssl
import urllib.error
import urllib.request

from .utils import ejecutar

DIRECTORIOS_APACHE = (
    ('/etc/apache2/sites-available', '/etc/apache2/sites-enabled'),   # Debian / Ubuntu
    ('/etc/httpd/conf.d', '/etc/httpd/conf.d'),                       # CentOS / RHEL
)

DIRECTORIOS_NGINX = (
    ('/etc/nginx/sites-available', '/etc/nginx/sites-enabled'),
    ('/etc/nginx/conf.d', '/etc/nginx/conf.d'),
)

SERVICIOS_WEB = ('apache2', 'nginx', 'httpd')

_RE_DIRECTIVA = {
    'servername': re.compile(r'^\s*ServerName\s+(\S+)', re.I | re.M),
    'serveralias': re.compile(r'^\s*ServerAlias\s+(.+)$', re.I | re.M),
    'cert': re.compile(r'^\s*SSLCertificateFile\s+(\S+)', re.I | re.M),
    'documentroot': re.compile(r'^\s*DocumentRoot\s+"?([^"\s]+)', re.I | re.M),
    'proxy': re.compile(r'^\s*ProxyPass\s+\S+\s+\S+?://[\w\.\-]+:(\d+)', re.I | re.M),
    'proxy_unix': re.compile(r'^\s*ProxyPass\s+\S+\s+unix:(\S+?)\|', re.I | re.M),
    'redirect': re.compile(r'^\s*Redirect\s+permanent', re.I | re.M),
}

_RE_ERRORLOG = re.compile(r'^\s*ErrorLog\s+"?([^"\s]+)', re.I | re.M)
_RE_CUSTOMLOG = re.compile(r'^\s*CustomLog\s+"?([^"\s]+)', re.I | re.M)


def _leer(ruta):
    try:
        with open(ruta, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def _uno(clave, contenido):
    """Primer valor capturado por una directiva del vhost (o None)."""
    encontrado = _RE_DIRECTIVA[clave].search(contenido)
    return encontrado.group(1) if encontrado else None


def cargar_vhosts(config=None):
    """Lee una sola vez todos los vhost del servidor y los devuelve parseados.

    Los directorios se pueden fijar en config.json con "apache_dirs":
    [["/ruta/sites-available", "/ruta/sites-enabled"], ...]
    """
    directorios = DIRECTORIOS_APACHE
    if config and config.get('apache_dirs'):
        directorios = [tuple(par) for par in config['apache_dirs']]
    vhosts = []
    vistos = set()
    for disponibles, habilitados in directorios:
        if not os.path.isdir(disponibles):
            continue
        activos = set()
        if os.path.isdir(habilitados):
            activos = {os.path.basename(p) for p in glob.glob(os.path.join(habilitados, '*'))}
        # Se leen los de sites-available y, además, los archivos reales que
        # sólo existan en sites-enabled (algunos servidores los crean ahí).
        archivos = sorted(glob.glob(os.path.join(disponibles, '*.conf')))
        if os.path.isdir(habilitados) and habilitados != disponibles:
            for enlace in sorted(glob.glob(os.path.join(habilitados, '*.conf'))):
                if not os.path.islink(enlace):
                    archivos.append(enlace)
        for archivo in archivos:
            if os.path.realpath(archivo) in vistos:
                continue
            vistos.add(os.path.realpath(archivo))
            contenido = _leer(archivo)
            if not contenido:
                continue
            alias = []
            for linea in _RE_DIRECTIVA['serveralias'].findall(contenido):
                alias.extend(linea.split())
            proxies = [int(p) for p in _RE_DIRECTIVA['proxy'].findall(contenido)]
            sockets_unix = _RE_DIRECTIVA['proxy_unix'].findall(contenido)
            nombre_archivo = os.path.basename(archivo)
            certificado = _uno('cert', contenido)
            try:
                modificado = datetime.datetime.fromtimestamp(
                    os.stat(archivo).st_mtime).strftime('%Y-%m-%d %H:%M')
            except OSError:
                modificado = None
            vhosts.append({
                'servidor': 'apache',
                'archivo': archivo,
                'nombre': nombre_archivo,
                'sitio_archivo': nombre_archivo,
                'dir_disponibles': disponibles,
                'dir_habilitados': habilitados,
                'sitio': os.path.basename(archivo)[:-5],   # sin .conf, para a2ensite
                'habilitado': os.path.basename(archivo) in activos,
                'servername': _uno('servername', contenido),
                'alias': alias,
                'certificado': certificado,
                'documentroot': _uno('documentroot', contenido),
                'puertos_proxy': proxies,
                'sockets_unix': sockets_unix,
                'ssl': bool(certificado),
                'errorlog': [r for r in _RE_ERRORLOG.findall(contenido) if not r.startswith('|')],
                'customlog': [r for r in _RE_CUSTOMLOG.findall(contenido) if not r.startswith('|')],
                'modificado': modificado,
                'contenido': contenido,
            })
    vhosts.extend(_vhosts_nginx(config))
    return vhosts


VHOSTS_GENERICOS = ('000-default', 'default-ssl', 'default', '000-default-le-ssl')
UMBRAL_COINCIDENCIA = 60      # puntaje mínimo para dar el vhost por bueno
UMBRAL_DEBIL = 40             # por debajo del umbral, se marca como dudoso



# ---------------------------------------------------------------------- nginx
_RE_NG_SERVER_NAME = re.compile(r'^\s*server_name\s+([^;]+);', re.I | re.M)
_RE_NG_CERT = re.compile(r'^\s*ssl_certificate\s+([^;]+);', re.I | re.M)
_RE_NG_PROXY = re.compile(r'proxy_pass\s+\S+?://[\w\.\-]+:(\d+)', re.I)
_RE_NG_PROXY_UNIX = re.compile(r'proxy_pass\s+http://unix:([^;:]+)', re.I)
_RE_NG_ROOT = re.compile(r'^\s*root\s+([^;]+);', re.I | re.M)
_RE_NG_ACCESS = re.compile(r'^\s*access_log\s+([^;\s]+)', re.I | re.M)
_RE_NG_ERROR = re.compile(r'^\s*error_log\s+([^;\s]+)', re.I | re.M)


def _bloques_server(contenido):
    """Extrae el texto de cada bloque `server { ... }` contando llaves."""
    bloques = []
    for encontrado in re.finditer(r'\bserver\s*\{', contenido):
        inicio = encontrado.end()
        nivel, i = 1, inicio
        while i < len(contenido) and nivel:
            if contenido[i] == '{':
                nivel += 1
            elif contenido[i] == '}':
                nivel -= 1
            i += 1
        bloques.append(contenido[inicio:i - 1])
    return bloques


def _vhosts_nginx(config=None):
    """Lee los server blocks de nginx con la misma forma que los de Apache."""
    directorios = DIRECTORIOS_NGINX
    if config and config.get('nginx_dirs'):
        directorios = [tuple(par) for par in config['nginx_dirs']]

    sitios = []
    vistos = set()
    for disponibles, habilitados in directorios:
        if not os.path.isdir(disponibles):
            continue
        activos = set()
        if os.path.isdir(habilitados):
            for enlace in glob.glob(os.path.join(habilitados, '*')):
                activos.add(os.path.basename(enlace))
                try:
                    activos.add(os.path.basename(os.path.realpath(enlace)))
                except OSError:
                    pass
        for archivo in sorted(glob.glob(os.path.join(disponibles, '*'))):
            if not os.path.isfile(archivo) or archivo in vistos:
                continue
            vistos.add(archivo)
            contenido = _leer(archivo)
            if 'server' not in contenido:
                continue
            nombres = []
            certificado = None
            proxies = []
            sockets_unix = []
            for bloque in _bloques_server(contenido):
                for linea in _RE_NG_SERVER_NAME.findall(bloque):
                    nombres.extend([n for n in linea.split() if n not in ('_', 'localhost')])
                certificado = certificado or _uno_re(_RE_NG_CERT, bloque)
                proxies.extend(int(p) for p in _RE_NG_PROXY.findall(bloque))
                sockets_unix.extend(_RE_NG_PROXY_UNIX.findall(bloque))
            if not nombres and not proxies:
                continue
            try:
                modificado = datetime.datetime.fromtimestamp(
                    os.stat(archivo).st_mtime).strftime('%Y-%m-%d %H:%M')
            except OSError:
                modificado = None
            nombre = os.path.basename(archivo)
            sitios.append({
                'servidor': 'nginx',
                'archivo': archivo,
                'nombre': nombre,
                'sitio': nombre[:-5] if nombre.endswith('.conf') else nombre,
                'sitio_archivo': nombre,
                'dir_disponibles': disponibles,
                'dir_habilitados': habilitados,
                'habilitado': (nombre in activos) or (disponibles == habilitados),
                'servername': nombres[0] if nombres else None,
                'alias': nombres[1:],
                'certificado': (certificado or '').strip() or None,
                'documentroot': (_uno_re(_RE_NG_ROOT, contenido) or '').strip().rstrip(';') or None,
                'puertos_proxy': sorted(set(proxies)),
                'sockets_unix': [r.strip() for r in sockets_unix],
                'ssl': bool(certificado),
                'errorlog': [r for r in _RE_NG_ERROR.findall(contenido) if not r.startswith('|')],
                'customlog': [r for r in _RE_NG_ACCESS.findall(contenido) if not r.startswith('|')],
                'modificado': modificado,
                'contenido': contenido,
            })
    return sitios


def _uno_re(expresion, contenido):
    encontrado = expresion.search(contenido)
    return encontrado.group(1) if encontrado else None


def estado_servidores_web(config=None):
    """Estado de los demonios web instalados (apache2 / nginx)."""
    from . import systemd
    salida = {}
    for unidad in SERVICIOS_WEB:
        estado = systemd.estado_servicio(unidad, timeout=8)
        if estado.get('existe'):
            salida[unidad] = {'activo': estado.get('activo'), 'estado': estado.get('estado'),
                              'uptime': estado.get('uptime'), 'desde': estado.get('desde')}
    return salida


def buscar_vhost(instancia, vhosts, puerto_servicio=None, socket_unix=None):
    """Empareja una instancia con su vhost de Apache.

    Se puntúa por señales fuertes primero (el puerto al que hace ProxyPass el
    vhost y la ruta de la instalación dentro del archivo), porque el dominio de
    credenciales.json suele quedar desactualizado al clonar el template.
    """
    dominio = (instancia.dominio or '').lower()
    candidatos = []
    for vhost in vhosts:
        puntaje = 0
        motivos = []
        nombres = [(vhost.get('servername') or '').lower()] + [a.lower() for a in vhost['alias']]
        nombres = [n for n in nombres if n]

        if puerto_servicio and puerto_servicio in vhost['puertos_proxy']:
            puntaje += 100
            motivos.append('proxy al puerto %s' % puerto_servicio)
        socks = vhost.get('sockets_unix') or []
        if socket_unix and socket_unix in socks:
            puntaje += 100
            motivos.append('proxy al socket %s' % socket_unix)
        elif socks and any(instancia.cliente.lower() in s.lower() for s in socks):
            puntaje += 80
            motivos.append('socket unix con el nombre del cliente')
        # El primer segmento del ServerName suele ser el cliente
        # (elmaestro.integrasoluc.net), señal fiable cuando credenciales.json
        # quedó con el dominio del template.
        if any(n.split('.')[0] == instancia.cliente.lower() for n in nombres):
            puntaje += 60
            motivos.append('el ServerName empieza por el nombre del cliente')
        if instancia.ruta and instancia.ruta in vhost['contenido']:
            puntaje += 80
            motivos.append('ruta de la instalación en el vhost')
        if dominio and dominio in nombres:
            puntaje += 60
            motivos.append('ServerName coincide con credenciales.json')
        elif dominio and any(dominio in n for n in nombres):
            puntaje += 30
            motivos.append('dominio parecido al ServerName')
        if instancia.cliente.lower() in vhost['sitio'].lower():
            puntaje += 40
            motivos.append('nombre del archivo')

        if vhost['sitio'].lower() in VHOSTS_GENERICOS:
            puntaje -= 200   # el vhost por defecto de Apache nunca es la respuesta
        if not puntaje:
            continue
        candidatos.append((puntaje, 1 if vhost['ssl'] else 0, vhost, motivos))

    # Por debajo del umbral no se afirma nada, pero si hay una coincidencia
    # débil se devuelve marcada para no dejar la instancia "sin vhost" a ciegas.
    fuertes = [c for c in candidatos if c[0] >= UMBRAL_COINCIDENCIA]
    debiles = [c for c in candidatos if UMBRAL_DEBIL <= c[0] < UMBRAL_COINCIDENCIA]
    candidatos = fuertes or debiles
    if not candidatos:
        return None
    debil = not fuertes
    candidatos.sort(key=lambda c: (c[0], c[1]), reverse=True)

    puntaje, _, mejor, motivos = candidatos[0]
    elegido = dict(mejor)
    elegido.pop('contenido', None)
    elegido['coincidencia'] = puntaje
    elegido['motivos'] = motivos
    elegido['dudoso'] = debil
    elegido['otros'] = [
        {'nombre': v['nombre'], 'habilitado': v['habilitado'], 'ssl': v['ssl'],
         'servername': v.get('servername'), 'servidor': v.get('servidor')}
        for p, _s, v, _m in candidatos[1:4]
    ]
    return elegido


def diagnostico(instancia, vhosts, puerto_servicio=None, socket_unix=None, limite=8):
    """Explica el emparejamiento: qué vhosts se leyeron y qué puntaje sacó cada uno."""
    dominio = (instancia.dominio or '').lower()
    detalle = []
    for vhost in vhosts:
        nombres = [(vhost.get('servername') or '').lower()] + [a.lower() for a in vhost['alias']]
        nombres = [n for n in nombres if n]
        motivos, puntaje = [], 0
        if puerto_servicio and puerto_servicio in vhost['puertos_proxy']:
            puntaje += 100; motivos.append('puerto %s' % puerto_servicio)
        socks = vhost.get('sockets_unix') or []
        if socket_unix and socket_unix in socks:
            puntaje += 100; motivos.append('socket %s' % socket_unix)
        elif socks and any(instancia.cliente.lower() in s.lower() for s in socks):
            puntaje += 80; motivos.append('socket con el nombre del cliente')
        if instancia.ruta and instancia.ruta in vhost['contenido']:
            puntaje += 80; motivos.append('ruta de la instalación')
        if dominio and dominio in nombres:
            puntaje += 60; motivos.append('ServerName = dominio de credenciales')
        elif dominio and any(dominio in n for n in nombres):
            puntaje += 30; motivos.append('dominio parecido')
        if any(n.split('.')[0] == instancia.cliente.lower() for n in nombres):
            puntaje += 60; motivos.append('ServerName empieza por el cliente')
        if instancia.cliente.lower() in vhost['sitio'].lower():
            puntaje += 40; motivos.append('nombre del archivo')
        if vhost['sitio'].lower() in VHOSTS_GENERICOS:
            puntaje -= 200; motivos.append('vhost por defecto (descartado)')
        detalle.append({
            'archivo': vhost['archivo'], 'servidor': vhost.get('servidor'),
            'servername': vhost.get('servername'), 'alias': vhost.get('alias'),
            'puertos': vhost.get('puertos_proxy'), 'sockets': vhost.get('sockets_unix'),
            'habilitado': vhost.get('habilitado'), 'puntaje': puntaje, 'motivos': motivos,
        })
    detalle.sort(key=lambda d: d['puntaje'], reverse=True)
    return {
        'cliente': instancia.cliente,
        'dominio_credenciales': dominio or None,
        'puerto_servicio': puerto_servicio,
        'socket_unix': socket_unix,
        'ruta': instancia.ruta,
        'vhosts_leidos': len(vhosts),
        'umbral': UMBRAL_COINCIDENCIA,
        'candidatos': detalle[:limite],
    }


def dominio_efectivo(instancia, vhost):
    """Dominio real de la instancia: manda el ServerName del vhost de Apache.

    credenciales.json arrastra el DOMINIO_GENERAL del template en muchas
    instalaciones, por eso sólo se usa como respaldo.
    """
    dominio_credenciales = (instancia.dominio or '').strip()
    dominio_apache = ((vhost or {}).get('servername') or '').strip()
    dominio = dominio_apache or dominio_credenciales
    if not dominio:
        return {'dominio': None, 'url': None, 'origen': None,
                'dominio_credenciales': dominio_credenciales or None,
                'dominio_apache': None, 'desactualizado': False}

    if dominio_apache:
        origen = 'apache'
        esquema = 'https' if (vhost or {}).get('ssl') else 'http'
    else:
        origen = 'credenciales'
        esquema = 'https' if instancia.credenciales.get('USE_SSL') else 'http'

    desactualizado = bool(dominio_apache and dominio_credenciales
                          and dominio_apache.lower() != dominio_credenciales.lower()
                          and dominio_credenciales.lower() not in
                          [a.lower() for a in (vhost or {}).get('alias', [])])
    return {
        'dominio': dominio,
        'url': '%s://%s' % (esquema, dominio),
        'origen': origen,
        'dominio_credenciales': dominio_credenciales or None,
        'dominio_apache': dominio_apache or None,
        'desactualizado': desactualizado,
    }


def _ruta_certificado(instancia, vhost, dominio=None):
    if vhost and vhost.get('certificado') and os.path.isfile(vhost['certificado']):
        return vhost['certificado']
    dominio = dominio or instancia.dominio
    if dominio:
        for nombre in ('fullchain.pem', 'cert.pem'):
            ruta = '/etc/letsencrypt/live/%s/%s' % (dominio, nombre)
            if os.path.isfile(ruta):
                return ruta
    return None


def info_certificado(instancia, vhost, dominio=None):
    """Datos del certificado SSL instalado (leído con openssl, sin dependencias)."""
    datos = {'tiene_ssl': False, 'archivo': None, 'emisor': None, 'valido_hasta': None,
             'dias_restantes': None, 'emitido': None, 'estado': 'sin-certificado', 'autofirmado': False,
             'coincide_dominio': None, 'error': None}

    ruta = _ruta_certificado(instancia, vhost, dominio)
    if not ruta:
        if vhost and vhost.get('certificado'):
            datos['error'] = 'El vhost apunta a %s pero el archivo no existe' % vhost['certificado']
        return datos

    datos['archivo'] = ruta
    codigo, salida, error = ejecutar(
        ['openssl', 'x509', '-in', ruta, '-noout', '-startdate', '-enddate', '-issuer', '-subject'],
        timeout=10)
    if codigo != 0:
        datos['error'] = error or 'no se pudo leer el certificado'
        return datos

    datos['tiene_ssl'] = True
    for linea in salida.splitlines():
        if linea.startswith('notBefore='):
            texto = linea.split('=', 1)[1].strip()
            for formato in ('%b %d %H:%M:%S %Y %Z', '%b %d %H:%M:%S %Y'):
                try:
                    datos['emitido'] = datetime.datetime.strptime(
                        texto.replace(' GMT', ''), formato.replace(' %Z', '')).strftime('%Y-%m-%d')
                    break
                except ValueError:
                    continue
        elif linea.startswith('notAfter='):
            texto = linea.split('=', 1)[1].strip()
            try:
                vence = datetime.datetime.strptime(texto, '%b %d %H:%M:%S %Y %Z')
            except ValueError:
                try:
                    vence = datetime.datetime.strptime(texto.replace(' GMT', ''), '%b %d %H:%M:%S %Y')
                except ValueError:
                    vence = None
            if vence:
                datos['valido_hasta'] = vence.strftime('%Y-%m-%d')
                datos['dias_restantes'] = (vence - datetime.datetime.utcnow()).days
        elif linea.startswith('issuer='):
            emisor = linea.split('=', 1)[1]
            encontrado = re.search(r'(?:CN\s*=\s*|CN=)([^,/]+)', emisor)
            datos['emisor'] = (encontrado.group(1).strip() if encontrado else emisor.strip())
        elif linea.startswith('subject='):
            encontrado = re.search(r'(?:CN\s*=\s*|CN=)([^,/]+)', linea)
            if encontrado:
                datos['dominio_certificado'] = encontrado.group(1).strip()

    # Snakeoil / autofirmado: emisor igual al sujeto o el certificado por defecto.
    cn_cert = datos.get('dominio_certificado')
    datos['autofirmado'] = bool(
        'snakeoil' in ruta.lower()
        or (cn_cert and datos.get('emisor') and cn_cert == datos['emisor']))

    objetivo = (dominio or instancia.dominio or '').lower()
    if cn_cert and objetivo:
        cn = cn_cert.lower().lstrip('*.')
        datos['coincide_dominio'] = objetivo == cn or objetivo.endswith('.' + cn)

    dias = datos['dias_restantes']
    if datos['autofirmado']:
        datos['estado'] = 'autofirmado'
    elif dias is None:
        datos['estado'] = 'desconocido'
    elif dias < 0:
        datos['estado'] = 'vencido'
    elif dias <= 15:
        datos['estado'] = 'por-vencer'
    else:
        datos['estado'] = 'vigente'
    return datos


def verificar_url(url, timeout=6):
    """Golpea la URL pública y reporta código HTTP y validez del certificado."""
    datos = {'url': url, 'responde': False, 'codigo': None, 'ssl_valido': None,
             'tiempo_ms': None, 'error': None}
    if not url:
        datos['error'] = 'sin dominio configurado'
        return datos

    inicio = datetime.datetime.now()
    peticion = urllib.request.Request(url, method='GET',
                                      headers={'User-Agent': 'integrasolucadminvps'})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            datos['codigo'] = respuesta.getcode()
            datos['responde'] = True
            datos['ssl_valido'] = True if url.startswith('https://') else None
    except urllib.error.HTTPError as ex:
        # Responde, aunque sea 404/500: el servicio y el TLS están arriba.
        datos['codigo'] = ex.code
        datos['responde'] = True
        datos['ssl_valido'] = True if url.startswith('https://') else None
    except ssl.SSLError as ex:
        datos['ssl_valido'] = False
        datos['error'] = 'SSL: %s' % ex
    except urllib.error.URLError as ex:
        motivo = getattr(ex, 'reason', ex)
        if isinstance(motivo, ssl.SSLCertVerificationError):
            datos['ssl_valido'] = False
            datos['error'] = 'Certificado inválido: %s' % motivo.verify_message
        else:
            datos['error'] = str(motivo)
    except (socket.timeout, TimeoutError):
        datos['error'] = 'timeout de %ss' % timeout
    except Exception as ex:  # pragma: no cover - defensivo
        datos['error'] = str(ex)

    datos['tiempo_ms'] = int((datetime.datetime.now() - inicio).total_seconds() * 1000)
    return datos
