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
    cabecera = ('CLIENTE', 'SISTEMA', 'SERVICIO', 'APACHE', 'SSL', 'URL',
                'BASE', 'TAM.BD', 'MEDIA', 'ULT.AUDIT.', 'ULT.SESION', '1a VENTA', 'ULT.VENTA')
    anchos = [14, 11, 11, 11, 10, 9, 7, 9, 9, 10, 16, 10, 10]

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
            '-' if db.get('desactivado') else ('activa' if r.get('db_ok') else 'CAIDA'),
            r.get('db_tamano'), r.get('media_tamano'),
            r.get('auditoria_fecha'), (r.get('ultima_sesion') or '')[:19],
            r.get('primera_venta'), r.get('ultima_venta'),
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


def _credenciales(usuario, clave):
    """Actualiza usuario/clave del panel en config.json (clave como hash)."""
    import json
    import os
    from app.config import CONFIG_PATH, EJEMPLO_PATH, hash_password

    origen = CONFIG_PATH if os.path.isfile(CONFIG_PATH) else EJEMPLO_PATH
    with open(origen, 'r', encoding='utf-8') as fh:
        datos = json.load(fh)
    datos.setdefault('auth', {})
    datos['auth']['enabled'] = True
    if usuario:
        datos['auth']['username'] = usuario
    if clave:
        datos['auth']['password_hash'] = hash_password(clave)
        datos['auth']['password'] = ''
    with open(CONFIG_PATH, 'w', encoding='utf-8') as fh:
        json.dump(datos, fh, indent=2, ensure_ascii=False)
    os.chmod(CONFIG_PATH, 0o600)
    print('Credenciales actualizadas en %s (usuario: %s).' % (CONFIG_PATH, datos['auth']['username']))
    print('Reinicia el panel:  systemctl restart integrasolucadmin')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Panel VPS Integrasoluc')
    parser.add_argument('--reporte', action='store_true', help='imprime el estado en consola y sale')
    parser.add_argument('--json', action='store_true', help='vuelca el estado en JSON y sale')
    parser.add_argument('--dev', action='store_true', help='usa el servidor de desarrollo de Flask')
    parser.add_argument('--usuario', help='cambia el usuario del panel en config.json')
    parser.add_argument('--clave', help='cambia la clave del panel (se guarda como hash sha256)')
    parser.add_argument('--host', help='dirección de escucha (por defecto la de config.json)')
    parser.add_argument('--port', type=int, help='puerto (por defecto el de config.json)')
    parser.add_argument('--version', action='version', version='integrasolucadminvps %s' % __version__)
    args = parser.parse_args(argv)

    if args.usuario or args.clave:
        return _credenciales(args.usuario, args.clave)

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
