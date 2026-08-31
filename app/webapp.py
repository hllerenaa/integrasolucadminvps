# -*- coding: utf-8 -*-
"""Aplicación web (Flask) del panel de administración."""
from __future__ import annotations

import datetime
import functools
import threading

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

from . import __version__, acciones as mod_acciones, exportar
from .collector import Colector
from .config import cargar


def crear_app(config=None):
    config = config or cargar()
    app = Flask(__name__)
    app.secret_key = config['secret_key']
    app.config['JSON_AS_ASCII'] = False
    app.config['PANEL'] = config

    colector = Colector(config)
    app.config['COLECTOR'] = colector

    # --------------------------------------------------------------- seguridad
    def autenticado():
        auth = config.get('auth') or {}
        if not auth.get('enabled'):
            return True
        if session.get('usuario'):
            return True
        token = auth.get('api_token') or ''
        if token:
            enviado = request.headers.get('X-API-Token') or request.args.get('token')
            if enviado and enviado == token:
                return True
        return False

    def requiere_login(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            if not autenticado():
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'no autorizado'}), 401
                return redirect(url_for('login', next=request.path))
            return vista(*args, **kwargs)
        return envoltura

    # ------------------------------------------------------------------ vistas
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        auth = config.get('auth') or {}
        if not auth.get('enabled'):
            return redirect(url_for('dashboard'))
        error = None
        if request.method == 'POST':
            usuario = (request.form.get('usuario') or '').strip()
            password = request.form.get('password') or ''
            if usuario == (auth.get('username') or 'admin') and config.verificar_password(password):
                session['usuario'] = usuario
                destino = request.args.get('next') or url_for('dashboard')
                return redirect(destino)
            error = 'Usuario o contraseña incorrectos'
        return render_template('login.html', error=error, titulo=config.get('titulo'),
                               version=__version__)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.route('/')
    @requiere_login
    def dashboard():
        return render_template(
            'dashboard.html',
            titulo=config.get('titulo'),
            version=__version__,
            auth_activa=bool((config.get('auth') or {}).get('enabled')),
            intervalo=int(config.get('intervalo_refresco') or 300),
        )

    @app.route('/api/estado')
    @requiere_login
    def api_estado():
        datos = colector.snapshot(tipo=request.args.get('tipo') or None,
                                  buscar=request.args.get('q') or None)
        datos['version'] = __version__
        datos['capacidades'] = {
            'bd': bool(config.get('consultar_bd', True)),
            'media': bool(config.get('medir_media', True)),
            'url': bool(config.get('verificar_url', True)),
            'acciones': bool((config.get('acciones') or {}).get('enabled', True)),
            'acciones_servicios': bool((config.get('acciones') or {}).get('servicios', True)),
            'acciones_apache': bool((config.get('acciones') or {}).get('apache', True)),
        }
        return jsonify(datos)

    @app.route('/api/instancia/<path:ident>')
    @requiere_login
    def api_instancia(ident):
        datos = colector.instancia(ident)
        if not datos:
            return jsonify({'error': 'instancia no encontrada'}), 404
        return jsonify(datos)

    @app.route('/api/refrescar', methods=['POST'])
    @requiere_login
    def api_refrescar():
        cuerpo = request.get_json(silent=True) or {}
        solo = cuerpo.get('solo') or request.args.get('solo')
        forzar_media = bool(cuerpo.get('media') or request.args.get('media'))

        def tarea():
            colector.refrescar(forzar=bool(solo), forzar_media=forzar_media, solo=solo)

        threading.Thread(target=tarea, name='refresco-manual', daemon=True).start()
        return jsonify({'ok': True, 'mensaje': 'Refresco iniciado'}), 202

    @app.route('/api/accion', methods=['POST'])
    @requiere_login
    def api_accion():
        cuerpo = request.get_json(silent=True) or request.form
        ident = cuerpo.get('id')
        accion = cuerpo.get('accion')
        datos = colector.instancia(ident) if ident else None
        if not datos:
            return jsonify({'ok': False, 'error': 'instancia no encontrada'}), 404

        resultado = mod_acciones.ejecutar_accion(
            config, datos, accion, usuario=session.get('usuario') or 'api')
        if not resultado.get('ok') and resultado.get('error'):
            return jsonify(resultado), 400

        # Se refresca sólo esa instancia para devolver el estado ya actualizado.
        colector.refrescar(forzar=True, solo=ident)
        resultado['instancia'] = colector.instancia(ident)
        return jsonify(resultado)

    @app.route('/api/acciones')
    @requiere_login
    def api_historial_acciones():
        return jsonify({'acciones': mod_acciones.historial(config)})

    @app.route('/export.csv')
    @requiere_login
    def export_csv():
        datos = colector.snapshot(tipo=request.args.get('tipo') or None,
                                  buscar=request.args.get('q') or None)
        return Response(
            exportar.a_csv(datos['instancias']),
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename=instancias.csv'},
        )

    @app.route('/export.xlsx')
    @requiere_login
    def export_xlsx():
        datos = colector.snapshot(tipo=request.args.get('tipo') or None,
                                  buscar=request.args.get('q') or None)
        contenido, error = exportar.a_xlsx(datos['instancias'], datos['resumen'])
        if error:
            return jsonify({'error': error}), 500
        nombre = 'instancias_%s.xlsx' % datetime.datetime.now().strftime('%Y%m%d_%H%M')
        return Response(
            contenido,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename=%s' % nombre},
        )

    @app.route('/healthz')
    def healthz():
        instantanea = colector.snapshot()
        return jsonify({
            'ok': True,
            'version': __version__,
            'instancias': instantanea['meta']['total'],
            'ultimo_refresco': instantanea['meta']['ultimo_refresco'],
        })

    colector.iniciar_en_segundo_plano()
    return app
