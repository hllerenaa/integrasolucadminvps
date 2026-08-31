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

_RE_DIRECTIVA = {
    'servername': re.compile(r'^\s*ServerName\s+(\S+)', re.I | re.M),
    'serveralias': re.compile(r'^\s*ServerAlias\s+(.+)$', re.I | re.M),
    'cert': re.compile(r'^\s*SSLCertificateFile\s+(\S+)', re.I | re.M),
    'documentroot': re.compile(r'^\s*DocumentRoot\s+"?([^"\s]+)', re.I | re.M),
    'proxy': re.compile(r'^\s*ProxyPass\s+\S+\s+\S+?://[\w\.\-]+:(\d+)', re.I | re.M),
    'redirect': re.compile(r'^\s*Redirect\s+permanent', re.I | re.M),
}


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
    for disponibles, habilitados in directorios:
        if not os.path.isdir(disponibles):
            continue
        activos = set()
        if os.path.isdir(habilitados):
            activos = {os.path.basename(p) for p in glob.glob(os.path.join(habilitados, '*'))}
        for archivo in sorted(glob.glob(os.path.join(disponibles, '*.conf'))):
            contenido = _leer(archivo)
            if not contenido:
                continue
            alias = []
            for linea in _RE_DIRECTIVA['serveralias'].findall(contenido):
                alias.extend(linea.split())
            proxies = [int(p) for p in _RE_DIRECTIVA['proxy'].findall(contenido)]
            certificado = _uno('cert', contenido)
            try:
                modificado = datetime.datetime.fromtimestamp(
                    os.stat(archivo).st_mtime).strftime('%Y-%m-%d %H:%M')
            except OSError:
                modificado = None
            vhosts.append({
                'archivo': archivo,
                'nombre': os.path.basename(archivo),
                'sitio': os.path.basename(archivo)[:-5],   # sin .conf, para a2ensite
                'habilitado': os.path.basename(archivo) in activos,
                'servername': _uno('servername', contenido),
                'alias': alias,
                'certificado': certificado,
                'documentroot': _uno('documentroot', contenido),
                'puertos_proxy': proxies,
                'ssl': bool(certificado),
                'modificado': modificado,
                'contenido': contenido,
            })
    return vhosts


VHOSTS_GENERICOS = ('000-default', 'default-ssl', 'default', '000-default-le-ssl')


def buscar_vhost(instancia, vhosts, puerto_servicio=None):
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
            puntaje += 25
            motivos.append('nombre del archivo')

        if vhost['sitio'].lower() in VHOSTS_GENERICOS:
            puntaje -= 200   # el vhost por defecto de Apache nunca es la respuesta
        if not puntaje:
            continue
        candidatos.append((puntaje, 1 if vhost['ssl'] else 0, vhost, motivos))

    # Sin una señal fuerte (puerto o ruta) no se afirma nada: mejor "sin vhost"
    # que atribuirle a un cliente el certificado de otro.
    candidatos = [c for c in candidatos if c[0] >= 60]
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (c[0], c[1]), reverse=True)

    puntaje, _, mejor, motivos = candidatos[0]
    elegido = dict(mejor)
    elegido.pop('contenido', None)
    elegido['coincidencia'] = puntaje
    elegido['motivos'] = motivos
    elegido['otros'] = [
        {'nombre': v['nombre'], 'habilitado': v['habilitado'], 'ssl': v['ssl'],
         'servername': v.get('servername')}
        for p, _s, v, _m in candidatos[1:4]
    ]
    return elegido


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
             'dias_restantes': None, 'estado': 'sin-certificado', 'autofirmado': False,
             'coincide_dominio': None, 'error': None}

    ruta = _ruta_certificado(instancia, vhost, dominio)
    if not ruta:
        if vhost and vhost.get('certificado'):
            datos['error'] = 'El vhost apunta a %s pero el archivo no existe' % vhost['certificado']
        return datos

    datos['archivo'] = ruta
    codigo, salida, error = ejecutar(
        ['openssl', 'x509', '-in', ruta, '-noout', '-enddate', '-issuer', '-subject'], timeout=10)
    if codigo != 0:
        datos['error'] = error or 'no se pudo leer el certificado'
        return datos

    datos['tiene_ssl'] = True
    for linea in salida.splitlines():
        if linea.startswith('notAfter='):
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
