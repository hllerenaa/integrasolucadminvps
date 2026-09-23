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
import shutil

from .utils import ejecutar, revisar_dns

RUTA_LIVE = '/etc/letsencrypt/live'
RUTA_RENOVACION = '/etc/letsencrypt/renewal'
SUFIJO_PAUSA = '.desactivado'

_RE_NOMBRE = re.compile(r'^\s*Certificate Name:\s*(.+)$', re.M)
_RE_DOMINIOS = re.compile(r'^\s*Domains:\s*(.+)$', re.M)
_RE_VENCE = re.compile(r'^\s*Expiry Date:\s*(\S+ \S+)', re.M)
_RE_RUTA = re.compile(r'^\s*Certificate Path:\s*(.+)$', re.M)
RE_DOMINIO = re.compile(r'^(?=.{4,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$')


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


def _dir_renovacion(config=None):
    return (config or {}).get('certbot_renewal_dir') or RUTA_RENOVACION


def estado_renovacion(nombre, config=None):
    """Si la renovación automática de un certificado está activa o pausada.

    Pausar = renombrar <nombre>.conf a <nombre>.conf.desactivado: el
    certificado sigue funcionando, pero `certbot renew` lo salta.
    """
    carpeta = _dir_renovacion(config)
    activo = os.path.join(carpeta, '%s.conf' % nombre)
    pausado = activo + SUFIJO_PAUSA
    if os.path.isfile(activo):
        return {'renovacion': 'activa', 'archivo': activo}
    if os.path.isfile(pausado):
        return {'renovacion': 'pausada', 'archivo': pausado}
    return {'renovacion': 'sin-config', 'archivo': None}


def pausar_renovacion(nombre, config=None):
    """Desactiva la renovación automática sin tocar el certificado."""
    datos = estado_renovacion(nombre, config)
    if datos['renovacion'] == 'pausada':
        return {'ok': True, 'renovacion': 'pausada', 'mensaje': 'Ya estaba pausada'}
    if datos['renovacion'] != 'activa':
        return {'ok': False, 'error': 'No se encontró la configuración de renovación de %s' % nombre}
    destino = datos['archivo'] + SUFIJO_PAUSA
    try:
        os.rename(datos['archivo'], destino)
    except OSError as ex:
        return {'ok': False, 'error': str(ex)}
    return {'ok': True, 'renovacion': 'pausada', 'archivo': destino,
            'mensaje': 'certbot renew ya no renovará %s (el certificado sigue instalado)' % nombre}


def reanudar_renovacion(nombre, config=None):
    """Vuelve a activar la renovación automática."""
    datos = estado_renovacion(nombre, config)
    if datos['renovacion'] == 'activa':
        return {'ok': True, 'renovacion': 'activa', 'mensaje': 'Ya estaba activa'}
    if datos['renovacion'] != 'pausada':
        return {'ok': False, 'error': 'No se encontró la configuración pausada de %s' % nombre}
    destino = datos['archivo'][:-len(SUFIJO_PAUSA)]
    try:
        os.rename(datos['archivo'], destino)
    except OSError as ex:
        return {'ok': False, 'error': str(ex)}
    return {'ok': True, 'renovacion': 'activa', 'archivo': destino,
            'mensaje': 'La renovación automática de %s vuelve a estar activa' % nombre}


def _restos(nombre, config=None):
    """Carpetas y archivos que quedan de un certificado."""
    posibles = [
        os.path.join(RUTA_LIVE, nombre),
        os.path.join(os.path.dirname(RUTA_LIVE), 'archive', nombre),
        os.path.join(_dir_renovacion(config), '%s.conf' % nombre),
        os.path.join(_dir_renovacion(config), '%s.conf%s' % (nombre, SUFIJO_PAUSA)),
    ]
    return [r for r in posibles if os.path.exists(r)]


def eliminar(nombre, config=None, forzar=False):
    """Elimina el certificado con certbot delete y comprueba que se fue.

    Si certbot no lo quita (versiones que ignoran --non-interactive, o
    restos en disco), se informa qué quedó; con forzar=True se borran esos
    restos a mano.
    """
    codigo, salida, error = ejecutar(
        ['certbot', 'delete', '--cert-name', nombre, '--non-interactive'], timeout=300)
    mensaje = (salida or error or '').strip()

    restos = _restos(nombre, config)
    borrados = []
    if restos and forzar:
        for resto in restos:
            try:
                if os.path.isdir(resto):
                    shutil.rmtree(resto)
                else:
                    os.remove(resto)
                borrados.append(resto)
            except OSError as ex:
                return {'ok': False, 'error': 'No se pudo borrar %s: %s' % (resto, ex),
                        'salida': mensaje, 'restos': restos}
        restos = _restos(nombre, config)

    if restos:
        return {
            'ok': False,
            'error': ('certbot terminó con código %s pero el certificado sigue en disco.'
                      % codigo),
            'salida': mensaje,
            'restos': restos,
            'puede_forzar': True,
        }

    return {'ok': True,
            'mensaje': 'Certificado %s eliminado%s'
                       % (nombre, ' (restos borrados a mano)' if borrados else ''),
            'salida': mensaje,
            'aviso': 'Revisa el vhost: si apuntaba a ese certificado, Apache/nginx no arrancará '
                     'hasta corregirlo o emitir uno nuevo.'}


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
        cert.update(estado_renovacion(cert['nombre'], config))
        vence = cert.get('vence')
        cert['emitido'] = cert['emitido'].strftime('%Y-%m-%d') if cert.get('emitido') else None
        if vence:
            cert['dias'] = (vence - ahora).days
            cert['vence'] = vence.strftime('%Y-%m-%d %H:%M')
        else:
            cert['dias'] = None
        dias = cert.get('dias')
        if cert.get('renovacion') == 'pausada':
            cert['estado'] = 'renovacion-pausada'
        elif dias is None:
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

    # Instancias con dominio que ningún certificado de Let's Encrypt cubre:
    # son las candidatas a «Emitir certificado».
    cubiertos = {d.lower() for c in certificados for d in c.get('dominios') or []}
    sin_certificado = []
    for inst in (instancias or []):
        dominio = (inst.get('dominio') or '').lower()
        if not dominio or dominio in cubiertos or not RE_DOMINIO.match(dominio):
            continue
        web = inst.get('apache') or {}
        if not web.get('archivo'):
            continue     # sin vhost certbot no tiene dónde instalarlo
        sin_certificado.append({
            'id': inst.get('id'), 'cliente': inst.get('cliente'), 'tipo': inst.get('tipo'),
            'dominio': dominio, 'servidor': web.get('servidor') or 'apache',
            'ssl': (inst.get('ssl') or {}).get('estado'),
            'oculta': bool(inst.get('oculta')),
        })
    sin_certificado.sort(key=lambda f: f['cliente'] or '')

    return {
        'sin_certificado': sin_certificado,
        'automatica': renovacion_automatica(),
        'certificados': certificados,
        'origen': origen,
        'total': len(certificados),
        'error': (error or salida or '').strip() if (codigo != 0 and not certificados) else None,
        'certbot': codigo == 0,
    }


def renovacion_automatica():
    """Si hay algo que renueve solo: el timer de systemd o el cron de certbot."""
    datos = {'timer': None, 'timer_activo': False, 'proxima': None, 'ultima': None,
             'cron': None}
    for timer in ('certbot.timer', 'snap.certbot.renew.timer'):
        codigo, salida, _ = ejecutar(
            ['systemctl', 'show', timer, '--no-pager',
             '--property=LoadState,ActiveState,NextElapseUSecRealtime,LastTriggerUSec'],
            timeout=10)
        if codigo != 0 or not salida:
            continue
        valores = dict(l.split('=', 1) for l in salida.splitlines() if '=' in l)
        if valores.get('LoadState') in (None, '', 'not-found', 'masked'):
            continue
        datos.update({'timer': timer, 'timer_activo': valores.get('ActiveState') == 'active',
                      'proxima': (valores.get('NextElapseUSecRealtime') or '').strip() or None,
                      'ultima': (valores.get('LastTriggerUSec') or '').strip() or None})
        break
    if os.path.isfile('/etc/cron.d/certbot'):
        datos['cron'] = '/etc/cron.d/certbot'
    datos['activa'] = bool(datos['timer_activo'] or datos['cron'])
    return datos


def _vencimiento(nombre):
    """Fecha de vencimiento actual del certificado (texto) o None."""
    ruta = os.path.join(RUTA_LIVE, nombre, 'fullchain.pem')
    vence = _fechas_openssl(ruta).get('vence')
    return vence.strftime('%Y-%m-%d %H:%M') if vence else None


def _servidores_activos(tarea):
    activos = []
    for demonio in ('apache2', 'nginx'):
        codigo, _salida = tarea.ejecutar(['systemctl', 'is-active', '--quiet', demonio],
                                         critico=False)
        if codigo == 0:
            activos.append(demonio)
    return activos


def recargar_web(tarea, config, modo='recargar', servidor=None):
    """Valida la configuración y recarga (o reinicia) Apache/nginx.

    Nunca se recarga una configuración que no pasa la prueba: un reload con
    errores deja caído el servidor web y con él todas las instancias.
    """
    verbo = 'restart' if modo == 'reiniciar' else 'reload'
    demonios = [servidor] if servidor else _servidores_activos(tarea)
    if not demonios:
        tarea.log('Ni apache2 ni nginx están activos: no hay nada que recargar', 'aviso')
        return True
    todo_ok = True
    for demonio in demonios:
        indice = tarea.paso('%s %s' % ('Reiniciar' if verbo == 'restart' else 'Recargar', demonio))
        prueba = ['nginx', '-t'] if demonio == 'nginx' else ['apache2ctl', 'configtest']
        codigo, _salida = tarea.ejecutar(prueba, critico=False, timeout=60)
        if codigo != 0:
            tarea.paso_error(indice, 'La configuración de %s tiene errores: no se toca' % demonio)
            todo_ok = False
            continue
        codigo, _salida = tarea.ejecutar(['systemctl', verbo, demonio], critico=False, timeout=120)
        if codigo != 0:
            tarea.paso_error(indice, 'systemctl %s %s devolvió %s' % (verbo, demonio, codigo))
            todo_ok = False
            continue
        codigo, salida = tarea.ejecutar(['systemctl', 'is-active', demonio], critico=False)
        if codigo == 0:
            tarea.paso_ok(indice, '%s activo' % demonio)
        else:
            tarea.paso_error(indice, '%s quedó %s' % (demonio, (salida or 'inactivo').strip()))
            todo_ok = False
    if not todo_ok:
        tarea.estado = 'error'
    return todo_ok


def renovar(tarea, config, nombre=None, forzar=False, simular=False):
    """Renueva uno o todos los certificados. Pensado para GestorTareas.

    Se compara el vencimiento antes y después para decir si de verdad se
    renovó: certbot termina bien aunque no haga nada (falta más de 30 días).
    """
    nombres = [nombre] if nombre else [c['nombre'] for c in listar(config)['certificados']]
    antes = {n: _vencimiento(n) for n in nombres}

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

    if simular:
        tarea.log('Prueba correcta: la renovación real funcionaría.', 'ok')
        return

    indice = tarea.paso('Comprobar las fechas de vencimiento')
    renovados = []
    for n in nombres:
        despues = _vencimiento(n)
        if despues and despues != antes.get(n):
            renovados.append(n)
            tarea.log('  %s: vencía %s → ahora vence %s' % (n, antes.get(n) or '?', despues), 'ok')
        elif nombre:
            tarea.log('  %s sigue venciendo %s: certbot no lo renovó (sólo renueva cuando '
                      'faltan menos de 30 días; usa «Forzar» si hace falta ya)'
                      % (n, despues or '?'), 'aviso')
    tarea.datos['renovados'] = renovados
    tarea.paso_ok(indice, '%s certificado(s) renovado(s)' % len(renovados))

    if renovados:
        recargar_web(tarea, config)
    else:
        tarea.log('No cambió ningún certificado: no hace falta recargar el servidor web.')


def emitir(tarea, config, dominio, servidor='apache', correo='', redirigir=True):
    """Emite un certificado nuevo con el plugin de Apache o nginx de certbot."""
    dominio = (dominio or '').strip().lower()
    if not RE_DOMINIO.match(dominio):
        tarea.log('Dominio inválido: %s' % dominio, 'error')
        tarea.estado = 'error'
        return

    indice = tarea.paso('Comprobar que %s apunta a este servidor' % dominio)
    dns = revisar_dns(dominio)
    if dns['ok']:
        tarea.paso_ok(indice, dns['mensaje'])
    elif not dns['ips']:
        tarea.paso_error(indice, dns['mensaje'])
        tarea.estado = 'error'
        return
    else:
        tarea.paso_ok(indice)
        tarea.log('  ' + dns['mensaje'], 'aviso')

    plugin = '--nginx' if servidor == 'nginx' else '--apache'
    comando = ['certbot', plugin, '-d', dominio, '--non-interactive', '--agree-tos']
    comando += ['--redirect'] if redirigir else ['--no-redirect']
    comando += ['-m', correo] if correo else ['--register-unsafely-without-email']
    indice = tarea.paso('Emitir el certificado de %s' % dominio)
    codigo, _salida = tarea.ejecutar(comando, timeout=600, critico=False)
    if codigo != 0:
        tarea.paso_error(indice, 'certbot devolvió el código %s' % codigo)
        tarea.estado = 'error'
        return
    vence = _vencimiento(dominio)
    tarea.paso_ok(indice, 'Certificado emitido%s' % (' (vence %s)' % vence if vence else ''))
    recargar_web(tarea, config, servidor='nginx' if servidor == 'nginx' else 'apache2')
