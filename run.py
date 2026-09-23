#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Punto de entrada del panel.

Uso:
    python3 run.py                 # levanta el servidor web (waitress)
    python3 run.py --reporte       # imprime en consola el estado de todas las instancias
    python3 run.py --json          # vuelca el estado completo en JSON
    python3 run.py --dev           # servidor de desarrollo de Flask
"""
from __future__ import annotations

import argparse
import json
import sys

from app import __version__
from app.collector import Colector
from app.config import cargar


def _reporte(colector):
    colector.refrescar()
    datos = colector.snapshot()
    filas = datos['instancias']
    cabecera = ('CLIENTE', 'SISTEMA', 'SERVICIO', 'WEB', 'SSL', 'URL', 'CPU%', 'RAM',
                'BASE', 'TAM.BD', 'MEDIA', 'LOGS', 'FACTURAS', 'ULT.AUDIT.', 'ULT.VENTA')
    anchos = [14, 11, 11, 10, 9, 9, 6, 9, 7, 9, 9, 9, 12, 10, 10]

    def linea(valores):
        return ' '.join(str(v if v is not None else '-')[:a].ljust(a)
                        for v, a in zip(valores, anchos))

    print(linea(cabecera))
    print('-' * (sum(anchos) + len(anchos) - 1))
    for inst in filas:
        r = inst.get('resumen') or {}
        db = inst.get('db') or {}
        print(linea((
            inst.get('cliente'), inst.get('tipo'), r.get('servicio_estado'),
            'activo' if r.get('apache_habilitado') else ('inactivo' if r.get('apache_archivo') else '-'),
            (str(r.get('ssl_dias')) + 'd') if r.get('ssl_dias') is not None else (r.get('ssl_estado') or '-'),
            ('HTTP %s' % r.get('url_codigo')) if r.get('url_responde') else 'no',
            r.get('cpu_pct'), r.get('ram_legible'),
            '-' if db.get('desactivado') else ('activa' if r.get('db_ok') else 'CAIDA'),
            r.get('db_tamano'), r.get('media_tamano'), r.get('logs_tamano'),
            ('%s (%s mes)' % (r.get('facturas_total'), r.get('facturas_mes'))
             if r.get('facturas_total') is not None else None),
            r.get('auditoria_fecha'), r.get('ultima_venta'),
        )))
    resumen = datos['resumen']
    con_bd = any(not (i.get('db') or {}).get('desactivado') for i in filas)
    print('-' * (sum(anchos) + len(anchos) - 1))
    partes = ['Instancias: %s' % resumen['total'],
              'Servicios activos: %s' % resumen['servicios_activos'],
              'Sitios Apache: %s' % resumen['sitios_habilitados'],
              'SSL vigentes: %s' % resumen['ssl_vigentes']]
    if con_bd:
        partes += ['Bases activas: %s' % resumen['db_activas'],
                   'BD: %s' % resumen['db_tamano']]
    partes.append('Media: %s' % resumen['media_tamano'])
    partes.append('Logs: %s' % resumen.get('logs_tamano', '-'))
    partes.append('RAM instancias: %s' % resumen.get('ram_tamano', '-'))
    disco = datos.get('disco') or {}
    if disco.get('total'):
        partes.append('Disco: %s de %s (%s%%)'
                      % (disco.get('usado'), disco.get('total'), disco.get('porcentaje')))
    print(' | '.join(partes))
    caidos = [i['cliente'] for i in filas if not (i.get('resumen') or {}).get('servicio_activo')]
    if caidos:
        print('Servicios NO activos: %s' % ', '.join(caidos))
    bases = [i['cliente'] for i in filas
             if not (i.get('db') or {}).get('desactivado')
             and not (i.get('resumen') or {}).get('db_ok')]
    if bases:
        print('Bases NO accesibles: %s' % ', '.join(bases))
    ssl_mal = ['%s (%s)' % (i['cliente'], (i.get('resumen') or {}).get('ssl_estado'))
               for i in filas if (i.get('resumen') or {}).get('ssl_estado') in ('vencido', 'por-vencer')]
    if ssl_mal:
        print('Certificados con problema: %s' % ', '.join(ssl_mal))
    sitios = [i['cliente'] for i in filas
              if (i.get('resumen') or {}).get('apache_archivo')
              and not (i.get('resumen') or {}).get('apache_habilitado')]
    if sitios:
        print('Sitios Apache desactivados: %s' % ', '.join(sitios))
    return 0 if not (caidos or bases or ssl_mal) else 1


def _leer_config():
    import json
    import os
    from app.config import CONFIG_PATH, EJEMPLO_PATH
    origen = CONFIG_PATH if os.path.isfile(CONFIG_PATH) else EJEMPLO_PATH
    with open(origen, 'r', encoding='utf-8') as fh:
        return json.load(fh), CONFIG_PATH


def _guardar_config(datos, ruta):
    import json
    import os
    with open(ruta, 'w', encoding='utf-8') as fh:
        json.dump(datos, fh, indent=2, ensure_ascii=False)
    os.chmod(ruta, 0o600)


def _listar_usuarios():
    from app.config import cargar
    config = cargar()
    print('%-20s %-16s %s' % ('USUARIO', 'VE EXCLUIDOS', 'ADMINISTRA EXCLUIDOS'))
    for datos in config.usuarios():
        print('%-20s %-16s %s' % (datos['usuario'],
                                  'sí' if datos['ver_excluidos'] else 'no',
                                  'sí' if datos['gestionar_excluidos'] else 'no'))
    return 0


def _quitar_usuario(usuario):
    datos, ruta = _leer_config()
    auth = datos.setdefault('auth', {})
    if auth.get('username') == usuario:
        print('No se puede quitar el usuario principal; cámbialo con --usuario/--clave.')
        return 1
    antes = len(auth.get('usuarios') or [])
    auth['usuarios'] = [u for u in (auth.get('usuarios') or []) if u.get('usuario') != usuario]
    if len(auth['usuarios']) == antes:
        print('No existe el usuario %s' % usuario)
        return 1
    _guardar_config(datos, ruta)
    print('Usuario %s eliminado. Reinicia el panel.' % usuario)
    return 0


def _credenciales(usuario, clave, ver_excluidos=False, gestionar_excluidos=False):
    """Crea o actualiza un usuario del panel (la clave se guarda como hash)."""
    from app.config import hash_password

    datos, ruta = _leer_config()
    auth = datos.setdefault('auth', {})
    auth['enabled'] = True
    principal = auth.get('username') or 'admin'

    if not usuario or usuario == principal:
        # Usuario principal (el histórico de auth.username)
        if usuario:
            auth['username'] = usuario
        if clave:
            auth['password_hash'] = hash_password(clave)
            auth['password'] = ''
        if ver_excluidos:
            auth['ver_excluidos'] = True
        if gestionar_excluidos:
            auth['gestionar_excluidos'] = True
        nombre = auth.get('username')
    else:
        lista = auth.setdefault('usuarios', [])
        entrada = next((u for u in lista if u.get('usuario') == usuario), None)
        if entrada is None:
            entrada = {'usuario': usuario}
            lista.append(entrada)
        if clave:
            entrada['password_hash'] = hash_password(clave)
            entrada['password'] = ''
        entrada['ver_excluidos'] = bool(ver_excluidos)
        entrada['gestionar_excluidos'] = bool(gestionar_excluidos)
        nombre = usuario

    _guardar_config(datos, ruta)
    print('Usuario "%s" guardado en %s' % (nombre, ruta))
    print('  ve las instancias excluidas: %s' % ('sí' if ver_excluidos else 'no'))
    print('  administra la lista:         %s' % ('sí' if gestionar_excluidos else 'no'))
    print('Reinicia el panel:  systemctl restart integrasolucadmin')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Panel VPS Integrasoluc')
    parser.add_argument('--reporte', action='store_true', help='imprime el estado en consola y sale')
    parser.add_argument('--json', action='store_true', help='vuelca el estado en JSON y sale')
    parser.add_argument('--dev', action='store_true', help='usa el servidor de desarrollo de Flask')
    parser.add_argument('--usuario', help='cambia el usuario del panel en config.json')
    parser.add_argument('--clave', help='cambia la clave del panel (se guarda como hash sha256)')
    parser.add_argument('--ver-excluidos', dest='ver_excluidos', action='store_true',
                        help='ese usuario ve también las instancias excluidas')
    parser.add_argument('--gestionar-excluidos', dest='gestionar_excluidos', action='store_true',
                        help='ese usuario puede administrar la lista de excluidos')
    parser.add_argument('--quitar-usuario', help='elimina un usuario del panel')
    parser.add_argument('--listar-usuarios', action='store_true', help='muestra los usuarios')
    parser.add_argument('--host', help='dirección de escucha (por defecto la de config.json)')
    parser.add_argument('--port', type=int, help='puerto (por defecto el de config.json)')
    parser.add_argument('--version', action='version', version='integrasolucadminvps %s' % __version__)
    args = parser.parse_args(argv)

    if args.listar_usuarios:
        return _listar_usuarios()
    if args.quitar_usuario:
        return _quitar_usuario(args.quitar_usuario)
    if args.usuario or args.clave:
        return _credenciales(args.usuario, args.clave,
                             args.ver_excluidos, args.gestionar_excluidos)

    config = cargar()
    if args.host:
        config['host'] = args.host
    if args.port:
        config['port'] = args.port

    if args.reporte or args.json:
        colector = Colector(config)
        if args.json:
            colector.refrescar()
            print(json.dumps(colector.snapshot(), indent=2, ensure_ascii=False, default=str))
            return 0
        return _reporte(colector)

    from app.webapp import crear_app
    app = crear_app(config)
    host, port = config['host'], int(config['port'])
    print('Panel disponible en http://%s:%s (config: %s)' % (host, port, config.path))

    if args.dev:
        app.run(host=host, port=port, debug=True, use_reloader=False)
        return 0

    from waitress import serve
    serve(app, host=host, port=port, threads=8, ident='integrasolucadminvps')
    return 0


if __name__ == '__main__':
    sys.exit(main())
