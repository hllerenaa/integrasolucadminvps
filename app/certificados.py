# -*- coding: utf-8 -*-
"""Certificados de Let's Encrypt: listarlos, ver vigencia y renovarlos.

Se usa `certbot certificates` cuando está disponible y, si no, se recorre
/etc/letsencrypt/live leyendo cada certificado con openssl.
"""
from __future__ import annotations

import datetime
import glob
import os
import re

from .utils import ejecutar

RUTA_LIVE = '/etc/letsencrypt/live'

_RE_NOMBRE = re.compile(r'^\s*Certificate Name:\s*(.+)$', re.M)
_RE_DOMINIOS = re.compile(r'^\s*Domains:\s*(.+)$', re.M)
_RE_VENCE = re.compile(r'^\s*Expiry Date:\s*(\S+ \S+)', re.M)
_RE_RUTA = re.compile(r'^\s*Certificate Path:\s*(.+)$', re.M)


def _fechas_openssl(ruta):
    """notBefore / notAfter de un certificado."""
    datos = {'emitido': None, 'vence': None, 'emisor': None}
    if not ruta or not os.path.isfile(ruta):
        return datos
    codigo, salida, _ = ejecutar(
        ['openssl', 'x509', '-in', ruta, '-noout', '-startdate', '-enddate', '-issuer'], timeout=10)
    if codigo != 0:
        return datos
    for linea in salida.splitlines():
        clave, _, valor = linea.partition('=')
        valor = valor.strip()
        if clave == 'notBefore':
            datos['emitido'] = _parsear(valor)
        elif clave == 'notAfter':
            datos['vence'] = _parsear(valor)
        elif clave == 'issuer':
            encontrado = re.search(r'CN\s*=\s*([^,/]+)', valor)
            datos['emisor'] = (encontrado.group(1).strip() if encontrado else valor)
    return datos


def _parsear(texto):
    for formato in ('%b %d %H:%M:%S %Y %Z', '%b %d %H:%M:%S %Y'):
        try:
            return datetime.datetime.strptime(texto.replace(' GMT', ''), formato.replace(' %Z', ''))
        except ValueError:
            continue
    return None


def _bloques_certbot(salida):
    """Parte la salida de `certbot certificates` en un bloque por certificado."""
    posiciones = [m.start() for m in _RE_NOMBRE.finditer(salida)]
    bloques = []
    for i, inicio in enumerate(posiciones):
        fin = posiciones[i + 1] if i + 1 < len(posiciones) else len(salida)
        bloques.append(salida[inicio:fin])
    return bloques


def listar(config=None, instancias=None):
    """Lista los certificados con sus fechas y a qué instancia pertenecen."""
    certificados = []
    origen = 'certbot'
    codigo, salida, error = ejecutar(['certbot', 'certificates'], timeout=90)

    if codigo == 0 and salida:
        for bloque in _bloques_certbot(salida):
            nombre = _RE_NOMBRE.search(bloque).group(1).strip()
            dominios = _RE_DOMINIOS.search(bloque)
            ruta = _RE_RUTA.search(bloque)
            ruta = ruta.group(1).strip() if ruta else os.path.join(RUTA_LIVE, nombre, 'fullchain.pem')
            fechas = _fechas_openssl(ruta)
            certificados.append({
                'nombre': nombre,
                'dominios': (dominios.group(1).split() if dominios else [nombre]),
                'archivo': ruta,
                'emitido': fechas['emitido'],
                'vence': fechas['vence'],
                'emisor': fechas['emisor'],
            })
    else:
        # Sin certbot (o sin permisos): se leen los certificados del disco.
        origen = 'disco'
        for carpeta in sorted(glob.glob(os.path.join(RUTA_LIVE, '*'))):
            if not os.path.isdir(carpeta):
                continue
            ruta = os.path.join(carpeta, 'fullchain.pem')
            if not os.path.isfile(ruta):
                ruta = os.path.join(carpeta, 'cert.pem')
            if not os.path.isfile(ruta):
                continue
            nombre = os.path.basename(carpeta)
            fechas = _fechas_openssl(ruta)
            certificados.append({
                'nombre': nombre, 'dominios': [nombre], 'archivo': ruta,
                'emitido': fechas['emitido'], 'vence': fechas['vence'],
                'emisor': fechas['emisor'],
            })

    ahora = datetime.datetime.utcnow()
    por_dominio = {}
    for inst in (instancias or []):
        for dominio in filter(None, [inst.get('dominio'), inst.get('dominio_apache'),
                                     inst.get('dominio_credenciales')]):
            por_dominio.setdefault(dominio.lower(), inst)

    for cert in certificados:
        vence = cert.get('vence')
        cert['emitido'] = cert['emitido'].strftime('%Y-%m-%d') if cert.get('emitido') else None
        if vence:
            cert['dias'] = (vence - ahora).days
            cert['vence'] = vence.strftime('%Y-%m-%d %H:%M')
        else:
            cert['dias'] = None
        dias = cert.get('dias')
        if dias is None:
            cert['estado'] = 'desconocido'
        elif dias < 0:
            cert['estado'] = 'vencido'
        elif dias <= 15:
            cert['estado'] = 'por-vencer'
        elif dias <= 30:
            cert['estado'] = 'renovable'
        else:
            cert['estado'] = 'vigente'
        # Instancia a la que pertenece
        inst = None
        for dominio in cert['dominios']:
            inst = por_dominio.get((dominio or '').lower())
            if inst:
                break
        cert['instancia'] = (inst or {}).get('id')
        cert['cliente'] = (inst or {}).get('cliente')

    certificados.sort(key=lambda c: (c['dias'] if c['dias'] is not None else 99999))
    return {
        'certificados': certificados,
        'origen': origen,
        'total': len(certificados),
        'error': (error or salida or '').strip() if (codigo != 0 and not certificados) else None,
        'certbot': codigo == 0,
    }


def renovar(tarea, config, nombre=None, forzar=False, simular=False):
    """Renueva uno o todos los certificados. Pensado para GestorTareas."""
    comando = ['certbot', 'renew']
    if nombre:
        comando += ['--cert-name', nombre]
    if simular:
        comando += ['--dry-run']
    if forzar and not simular:
        comando += ['--force-renewal']
    comando += ['--non-interactive']

    indice = tarea.paso('Renovar %s' % (nombre or 'todos los certificados'))
    codigo, _salida = tarea.ejecutar(comando, timeout=1800, critico=False)
    if codigo == 0:
        tarea.paso_ok(indice, 'certbot terminó correctamente')
    else:
        tarea.paso_error(indice, 'certbot devolvió el código %s' % codigo)
        tarea.estado = 'error'
        return

    if not simular:
        indice = tarea.paso('Recargar el servidor web')
        for demonio in ('apache2', 'nginx'):
            codigo_activo, _salida = tarea.ejecutar(['systemctl', 'is-active', demonio],
                                                    critico=False)
            if codigo_activo == 0:
                tarea.ejecutar(['systemctl', 'reload', demonio], critico=False)
        tarea.paso_ok(indice)
