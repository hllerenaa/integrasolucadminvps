# -*- coding: utf-8 -*-
"""Aplicación web (Flask) del panel de administración."""
from __future__ import annotations

import datetime
import functools
import threading

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

from . import (__version__, acciones as mod_acciones, aprovisionar,
               backups as mod_backups, certificados as mod_certificados,
               credenciales as mod_credenciales, cron as mod_cron, dbstats,
               discovery, excluidos as mod_excluidos, exportar, units)
from flask import send_file

from .utils import bytes_legible
from .tareas import GestorTareas
from .collector import Colector
from .config import cargar


def crear_app(config=None):
    config = config or cargar()
    app = Flask(__name__)
    app.secret_key = config['secret_key']
    app.config['JSON_AS_ASCII'] = False
    # Detrás de Apache/nginx con SSL conviene marcar la cookie como segura
    # (config.json: "session_cookie_secure": true). Con acceso por IP en HTTP
    # debe quedar en false o el login no persistiría.
    app.config['SESSION_COOKIE_SECURE'] = bool(config.get('session_cookie_secure'))
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PANEL'] = config

    colector = Colector(config)
    app.config['COLECTOR'] = colector
    tareas = GestorTareas(config)
    app.config['TAREAS'] = tareas

    # --------------------------------------------------------------- seguridad
    def permisos():
        """Permisos del usuario de la sesión (el token de API los tiene todos)."""
        if not (config.get('auth') or {}).get('enabled'):
            return {'ver_excluidos': True, 'gestionar_excluidos': True}
        if session.get('usuario'):
            return {'ver_excluidos': bool(session.get('ver_excluidos')),
                    'gestionar_excluidos': bool(session.get('gestionar_excluidos'))}
        return {'ver_excluidos': True, 'gestionar_excluidos': True}   # X-API-Token

    def requiere_gestionar_excluidos(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            if not permisos()['gestionar_excluidos']:
                if request.path.startswith('/api/'):
                    return jsonify({'ok': False, 'error': 'Tu usuario no administra la lista de '
                                                          'sistemas excluidos'}), 403
                return redirect(url_for('dashboard'))
            return vista(*args, **kwargs)
        return envoltura

    def requiere_ver_excluidos(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            if not permisos()['ver_excluidos']:
                if request.path.startswith('/api/'):
                    return jsonify({'ok': False, 'error': 'Tu usuario no tiene acceso al '
                                                          'historial de acciones'}), 403
                return redirect(url_for('dashboard'))
            return vista(*args, **kwargs)
        return envoltura

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
            datos = config.autenticar(usuario, password)
            if datos:
                session['usuario'] = datos['usuario']
                session['ver_excluidos'] = bool(datos.get('ver_excluidos'))
                session['gestionar_excluidos'] = bool(datos.get('gestionar_excluidos'))
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
            'dashboard.html', seccion='instancias', permisos=permisos(),
            titulo=config.get('titulo'),
            version=__version__,
            auth_activa=bool((config.get('auth') or {}).get('enabled')),
            intervalo=int(config.get('intervalo_refresco') or 300),
        )

    @app.route('/certificados')
    @requiere_login
    def pagina_certificados():
        return render_template('certificados.html', seccion='certificados', permisos=permisos(),
                               titulo=config.get('titulo'), version=__version__,
                               auth_activa=bool((config.get('auth') or {}).get('enabled')))

    @app.route('/backups')
    @requiere_login
    def pagina_backups():
        return render_template('backups.html', seccion='backups', permisos=permisos(),
                               titulo=config.get('titulo'), version=__version__,
                               auth_activa=bool((config.get('auth') or {}).get('enabled')))

    @app.route('/nueva')
    @requiere_login
    def pagina_nueva():
        return render_template('nueva.html', seccion='nueva', permisos=permisos(),
                               titulo=config.get('titulo'), version=__version__,
                               auth_activa=bool((config.get('auth') or {}).get('enabled')))

    def ambito_ocultas(modo_ocultas):
        """Qué instancias ocultas puede ver este usuario.

        Es la única puerta: la usan tanto /api/estado como las exportaciones,
        para que un Excel nunca contenga lo que la pantalla no muestra.
        """
        permiso = permisos()
        modo_ocultas = (modo_ocultas or '').lower()
        if not permiso['ver_excluidos'] and not permiso['gestionar_excluidos']:
            return False, False
        if permiso['ver_excluidos']:
            # Se incluyen (marcadas) tanto en el panel principal como en /excluidos.
            return True, modo_ocultas == 'solo'
        # Puede administrarlas pero no verlas en el panel: sólo en /excluidos.
        return modo_ocultas in ('1', 'todas', 'true'), modo_ocultas == 'solo'

    def instancias_pedidas(datos):
        """Recorta la instantánea a los identificadores que manda la pantalla.

        Llegan por POST en el mismo orden en que se ven en la tabla. El cliente
        sólo puede recortar: lo que el permiso dejó fuera no está en la
        instantánea y por lo tanto no puede volver a entrar por aquí.
        """
        cuerpo = request.get_json(silent=True) or {}
        ids = cuerpo.get('ids')
        if not isinstance(ids, list) or not ids:
            return datos['instancias']
        por_id = {i.get('id'): i for i in datos['instancias']}
        elegidas = [por_id[x] for x in ids if x in por_id]
        return elegidas or datos['instancias']

    @app.route('/api/estado')
    @requiere_login
    def api_estado():
        incluir, solo = ambito_ocultas(request.args.get('ocultas'))
        datos = colector.snapshot(tipo=request.args.get('tipo') or None,
                                  buscar=request.args.get('q') or None,
                                  incluir_ocultas=incluir, solo_ocultas=solo)
        datos['version'] = __version__
        datos['capacidades'] = {
            'bd': bool(config.get('consultar_bd', True)),
            'media': bool(config.get('medir_media', True)),
            'url': bool(config.get('verificar_url', True)),
            'acciones': bool((config.get('acciones') or {}).get('enabled', True)),
            'acciones_servicios': bool((config.get('acciones') or {}).get('servicios', True)),
            'acciones_apache': bool((config.get('acciones') or {}).get('apache', True)),
            'acciones_datos': bool((config.get('acciones') or {}).get('datos', True)),
            'acciones_certbot': bool((config.get('acciones') or {}).get('certbot', True)),
            'backups': bool((config.get('backups') or {}).get('enabled', True)),
            'ver_excluidos': permisos()['ver_excluidos'],
            'gestionar_excluidos': permisos()['gestionar_excluidos'],
            'usuario': session.get('usuario'),
            'logs': bool(config.get('medir_logs', True)),
            'credenciales': bool((config.get('credenciales') or {}).get('ver', True)),
            'credenciales_editar': bool((config.get('credenciales') or {}).get('editar', True)),
            'credenciales_secretos': bool((config.get('credenciales') or {}).get('mostrar_secretos', True)),
            'aprovisionar': bool((config.get('aprovisionamiento') or {}).get('enabled', True)),
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

    @app.route('/api/credenciales/<path:ident>')
    @requiere_login
    def api_credenciales(ident):
        cfg = config.get('credenciales') or {}
        if not cfg.get('ver', True):
            return jsonify({'ok': False, 'error': 'La lectura de credenciales está desactivada'}), 403
        datos = colector.instancia(ident)
        if not datos:
            return jsonify({'ok': False, 'error': 'instancia no encontrada'}), 404

        con_secretos = request.args.get('secretos') in ('1', 'true', 'si')
        if con_secretos and not cfg.get('mostrar_secretos', True):
            return jsonify({'ok': False, 'error': 'Mostrar contraseñas está desactivado'}), 403

        resultado = mod_credenciales.leer(datos, con_secretos=con_secretos)
        resultado['editable'] = bool(cfg.get('editar', True))
        if con_secretos:
            mod_acciones.registrar_evento(
                config, session.get('usuario') or 'api', 'credenciales_ver_claves',
                datos.get('cliente'), 0, resultado.get('archivo') or '')
        return jsonify(resultado)

    @app.route('/api/credenciales/<path:ident>', methods=['POST'])
    @requiere_login
    def api_credenciales_guardar(ident):
        cfg = config.get('credenciales') or {}
        if not cfg.get('editar', True):
            return jsonify({'ok': False, 'error': 'La edición de credenciales está desactivada'}), 403
        datos = colector.instancia(ident)
        if not datos:
            return jsonify({'ok': False, 'error': 'instancia no encontrada'}), 404

        cuerpo = request.get_json(silent=True) or {}
        resultado = mod_credenciales.guardar(datos, cuerpo.get('texto'))
        mod_acciones.registrar_evento(
            config, session.get('usuario') or 'api', 'credenciales_guardar',
            datos.get('cliente'), 0 if resultado.get('ok') else 1,
            ', '.join(resultado.get('claves_modificadas') or []) or resultado.get('error', ''))
        if not resultado.get('ok'):
            return jsonify(resultado), 400
        colector.refrescar(forzar=True, solo=ident)
        resultado['instancia'] = colector.instancia(ident)
        return jsonify(resultado)

    # --------------------------------------------------------- aprovisionamiento
    def _aprovisionamiento_activo():
        return bool((config.get('aprovisionamiento') or {}).get('enabled', True))

    @app.route('/api/aprovisionar/opciones')
    @requiere_login
    def api_aprovisionar_opciones():
        if not _aprovisionamiento_activo():
            return jsonify({'habilitado': False}), 403
        return jsonify(aprovisionar.opciones(config, colector))

    @app.route('/api/aprovisionar/validar', methods=['POST'])
    @requiere_login
    def api_aprovisionar_validar():
        if not _aprovisionamiento_activo():
            return jsonify({'error': 'Creación de instancias desactivada'}), 403
        cuerpo = request.get_json(silent=True) or {}
        revisiones = aprovisionar.validar(config, colector, cuerpo)
        return jsonify({'revisiones': revisiones,
                        'ok': not any(r['critico'] for r in revisiones)})

    @app.route('/api/aprovisionar/crear', methods=['POST'])
    @requiere_login
    def api_aprovisionar_crear():
        if not _aprovisionamiento_activo():
            return jsonify({'error': 'Creación de instancias desactivada'}), 403
        cuerpo = request.get_json(silent=True) or {}
        faltan = [c for c in ('tipo', 'cliente', 'base', 'puerto') if not cuerpo.get(c)]
        if faltan:
            return jsonify({'error': 'Faltan datos: %s' % ', '.join(faltan)}), 400

        titulo = 'Crear instancia %s (%s)' % (cuerpo.get('cliente'), cuerpo.get('tipo'))
        if cuerpo.get('simular'):
            titulo = '[simulación] ' + titulo
        tarea = tareas.crear('crear_instancia', titulo, cuerpo,
                             usuario=session.get('usuario') or 'api')
        mod_acciones.registrar_evento(config, tarea.creado_por, 'instancia_crear',
                                      cuerpo.get('cliente'), 0,
                                      'simulación' if cuerpo.get('simular') else 'tarea %s' % tarea.id)
        tareas.lanzar(tarea, lambda t: aprovisionar.crear_instancia(t, config, colector))
        return jsonify({'ok': True, 'tarea': tarea.id}), 202

    @app.route('/api/tareas')
    @requiere_login
    def api_tareas():
        return jsonify({'tareas': tareas.listar()})

    @app.route('/api/tarea/<ident>')
    @requiere_login
    def api_tarea(ident):
        tarea = tareas.obtener(ident)
        if not tarea:
            return jsonify({'error': 'tarea no encontrada'}), 404
        try:
            desde = int(request.args.get('desde') or 0)
        except ValueError:
            desde = 0
        return jsonify(tarea.as_dict(desde=desde))

    @app.route('/api/tarea/<ident>/deshacer', methods=['POST'])
    @requiere_login
    def api_tarea_deshacer(ident):
        if not _aprovisionamiento_activo():
            return jsonify({'error': 'Creación de instancias desactivada'}), 403
        original = tareas.obtener(ident)
        if not original:
            return jsonify({'error': 'tarea no encontrada'}), 404
        if original.estado == 'corriendo':
            return jsonify({'error': 'La tarea todavía está corriendo'}), 400
        if not original.deshacer:
            return jsonify({'error': 'Esa tarea no creó nada que se pueda deshacer'}), 400

        tarea = tareas.crear('deshacer', 'Deshacer %s' % original.titulo,
                             {'origen': ident}, usuario=session.get('usuario') or 'api')
        mod_acciones.registrar_evento(config, tarea.creado_por, 'instancia_deshacer',
                                      (original.datos or {}).get('cliente'), 0, ident)
        tareas.lanzar(tarea, lambda t: aprovisionar.deshacer(t, config, colector, original))
        return jsonify({'ok': True, 'tarea': tarea.id}), 202

    # ------------------------------------------------------------- excluidos
    def _inventario_excluibles():
        """Todas las instalaciones del servidor con su servicio y si están ocultas."""
        ocultos = mod_excluidos.cargar(config)
        unidades = units.cargar_unidades(config)
        filas = []
        for inst in discovery.descubrir(config):
            unidad = units.buscar_unidad(inst, unidades, inst.servicio)
            servicio = (unidad or {}).get('unidad') or inst.servicio
            filas.append({
                'cliente': inst.cliente,
                'tipo': inst.tipo,
                'ruta': inst.ruta,
                'servicio': servicio,
                'dominio': inst.dominio,
                'oculta': mod_excluidos.excluida(ocultos, inst.cliente, servicio),
            })
        filas.sort(key=lambda f: (not f['oculta'], f['cliente']))
        # Nombres del archivo que no corresponden a ninguna instalación
        conocidos = set()
        for fila in filas:
            conocidos.add(fila['cliente'].lower())
            conocidos.add((fila['servicio'] or '').lower())
        return filas, sorted(ocultos), sorted(n for n in ocultos if n not in conocidos)

    @app.route('/excluidos')
    @requiere_login
    @requiere_gestionar_excluidos
    def pagina_excluidos():
        _filas, nombres, huerfanos = _inventario_excluibles()
        return render_template(
            'excluidos.html', seccion='excluidos', permisos=permisos(),
            titulo=config.get('titulo'), version=__version__,
            auth_activa=bool((config.get('auth') or {}).get('enabled')),
            intervalo=int(config.get('intervalo_refresco') or 300),
            nombres=nombres, huerfanos=huerfanos,
            archivo=mod_excluidos.ruta(config), texto=', '.join(nombres))

    @app.route('/api/excluidos')
    @requiere_login
    def api_excluidos():
        filas, nombres, huerfanos = _inventario_excluibles()
        return jsonify({'archivo': mod_excluidos.ruta(config), 'nombres': nombres,
                        'huerfanos': huerfanos, 'instalaciones': filas})

    @app.route('/api/excluidos/alternar', methods=['POST'])
    @requiere_login
    @requiere_gestionar_excluidos
    def api_excluidos_alternar():
        cuerpo = request.get_json(silent=True) or {}
        cliente = (cuerpo.get('cliente') or '').strip().lower()
        servicio = (cuerpo.get('servicio') or '').strip().lower()
        ocultar = bool(cuerpo.get('ocultar'))
        if not cliente and not servicio:
            return jsonify({'ok': False, 'error': 'Falta el cliente o el servicio'}), 400

        nombres = list(mod_excluidos.cargar(config))
        if ocultar:
            # Se guarda el nombre del servicio (es el que el usuario reconoce);
            # si no hay, el del cliente.
            nombre = servicio or cliente
            if nombre not in nombres:
                nombres.append(nombre)
        else:
            nombres = [n for n in nombres if n not in (cliente, servicio)]

        try:
            resultado = mod_excluidos.guardar(config, sorted(nombres))
        except OSError as ex:
            return jsonify({'ok': False, 'error': 'No se pudo escribir el archivo: %s' % ex}), 500

        mod_acciones.registrar_evento(
            config, session.get('usuario') or 'api',
            'excluidos_ocultar' if ocultar else 'excluidos_mostrar',
            servicio or cliente, 0, ', '.join(resultado['nombres']))
        # La marca se aplica al instante sobre lo ya recolectado; el refresco
        # de fondo sólo hace falta para traer instalaciones nuevas.
        colector.remarcar_ocultas()
        threading.Thread(target=lambda: colector.refrescar(forzar=True),
                         name='refresco-excluidos', daemon=True).start()
        resultado['ok'] = True
        return jsonify(resultado)

    @app.route('/api/excluidos', methods=['POST'])
    @requiere_login
    @requiere_gestionar_excluidos
    def api_excluidos_guardar():
        cuerpo = request.get_json(silent=True) or {}
        if 'nombres' in cuerpo:
            nombres = cuerpo.get('nombres') or []
        else:
            nombres = mod_excluidos.desde_texto(cuerpo.get('texto'))
        try:
            resultado = mod_excluidos.guardar(config, nombres)
        except OSError as ex:
            return jsonify({'ok': False, 'error': 'No se pudo escribir el archivo: %s' % ex}), 500
        mod_acciones.registrar_evento(config, session.get('usuario') or 'api',
                                      'excluidos_guardar', resultado['archivo'], 0,
                                      ', '.join(resultado['nombres']))
        colector.remarcar_ocultas()
        threading.Thread(target=lambda: colector.refrescar(forzar=True),
                         name='refresco-excluidos', daemon=True).start()
        resultado['ok'] = True
        return jsonify(resultado)

    @app.route('/api/instancia/<path:ident>/api-cedula', methods=['POST'])
    @requiere_login
    def api_cambiar_api_cedula(ident):
        cfg = config.get('acciones') or {}
        if not cfg.get('enabled', True) or not cfg.get('datos', True):
            return jsonify({'ok': False, 'error': 'Los cambios en la base están desactivados'}), 403
        datos = colector.instancia(ident)
        if not datos:
            return jsonify({'ok': False, 'error': 'instancia no encontrada'}), 404

        cuerpo = request.get_json(silent=True) or {}
        activar = bool(cuerpo.get('activar'))
        instancias = {i.id: i for i in discovery.descubrir(config)}
        instancia = instancias.get(ident)
        if not instancia:
            return jsonify({'ok': False, 'error': 'no se pudo resolver la instalación'}), 404

        resultado = dbstats.cambiar_api_cedula(instancia, config, activar)
        mod_acciones.registrar_evento(
            config, session.get('usuario') or 'api',
            'api_cedula_activar' if activar else 'api_cedula_desactivar',
            datos.get('cliente'), 0 if resultado.get('ok') else 1,
            resultado.get('error') or ('%s = %s' % (resultado.get('columna'), activar)))
        if not resultado.get('ok'):
            return jsonify(resultado), 400
        colector.refrescar(forzar=True, solo=ident)
        resultado['instancia'] = colector.instancia(ident)
        return jsonify(resultado)

    @app.route('/api/certificados')
    @requiere_login
    def api_certificados():
        instantanea = colector.snapshot(incluir_ocultas=True)
        return jsonify(mod_certificados.listar(config, instantanea['instancias']))

    @app.route('/api/certificados/renovar', methods=['POST'])
    @requiere_login
    def api_certificados_renovar():
        cfg = config.get('acciones') or {}
        if not cfg.get('enabled', True) or not cfg.get('certbot', True):
            return jsonify({'ok': False, 'error': 'La renovación de certificados está desactivada'}), 403
        cuerpo = request.get_json(silent=True) or {}
        nombre = cuerpo.get('nombre')
        forzar = bool(cuerpo.get('forzar'))
        simular = bool(cuerpo.get('simular'))

        titulo = 'Renovar %s' % (nombre or 'todos los certificados')
        if simular:
            titulo = '[prueba] ' + titulo
        elif forzar:
            titulo += ' (forzado)'
        tarea = tareas.crear('certbot', titulo,
                             {'nombre': nombre, 'forzar': forzar, 'simular': simular},
                             usuario=session.get('usuario') or 'api')
        mod_acciones.registrar_evento(config, tarea.creado_por, 'certbot_renovar',
                                      nombre or 'todos', 0, 'tarea %s' % tarea.id)
        tareas.lanzar(tarea, lambda t: mod_certificados.renovar(
            t, config, nombre=nombre, forzar=forzar, simular=simular))
        return jsonify({'ok': True, 'tarea': tarea.id}), 202

    @app.route('/api/certificados/accion', methods=['POST'])
    @requiere_login
    def api_certificados_accion():
        cfg = config.get('acciones') or {}
        if not cfg.get('enabled', True) or not cfg.get('certbot', True):
            return jsonify({'ok': False, 'error': 'Las acciones sobre certificados están desactivadas'}), 403
        cuerpo = request.get_json(silent=True) or {}
        nombre = (cuerpo.get('nombre') or '').strip()
        accion = (cuerpo.get('accion') or '').strip()
        if not nombre:
            return jsonify({'ok': False, 'error': 'Falta el nombre del certificado'}), 400

        if accion == 'pausar':
            resultado = mod_certificados.pausar_renovacion(nombre, config)
        elif accion == 'reanudar':
            resultado = mod_certificados.reanudar_renovacion(nombre, config)
        elif accion == 'eliminar':
            resultado = mod_certificados.eliminar(nombre, config,
                                                  forzar=bool(cuerpo.get('forzar')))
        else:
            return jsonify({'ok': False, 'error': 'Acción no permitida: %s' % accion}), 400

        mod_acciones.registrar_evento(
            config, session.get('usuario') or 'api', 'certbot_%s' % accion, nombre,
            0 if resultado.get('ok') else 1,
            resultado.get('mensaje') or resultado.get('error') or '')
        return jsonify(resultado), (200 if resultado.get('ok') else 400)

    # ----------------------------------------------------------------- backups
    def _backups_activos():
        return bool((config.get('backups') or {}).get('enabled', True))

    @app.route('/api/backups')
    @requiere_login
    def api_backups():
        if not _backups_activos():
            return jsonify({'error': 'Los backups están desactivados'}), 403
        instantanea = colector.snapshot(incluir_ocultas=True)
        return jsonify(mod_backups.listar(config, instantanea['instancias']))

    @app.route('/api/bases')
    @requiere_login
    def api_bases():
        """Bases del servidor PostgreSQL y cuáles no tiene ninguna instancia."""
        instantanea = colector.snapshot(incluir_ocultas=True)
        instancias = {i.id: i for i in discovery.descubrir(config)}
        objeto = None
        for datos in instantanea['instancias']:
            if (datos.get('db') or {}).get('ok') and instancias.get(datos['id']):
                objeto = instancias[datos['id']]
                break
        if objeto is None:
            return jsonify({'ok': False, 'error': 'Ninguna instancia con base accesible',
                            'bases': []}), 200

        resultado = dbstats.listar_bases(objeto, config)
        en_uso = {}
        for datos in instantanea['instancias']:
            nombre = (datos.get('db') or {}).get('dbname')
            if nombre:
                en_uso[nombre] = {'cliente': datos.get('cliente'), 'tipo': datos.get('tipo'),
                                  'oculta': bool(datos.get('oculta'))}
        respaldos = mod_backups.listar(config, instantanea['instancias'])
        con_backup = {}
        for fila in respaldos['instancias']:
            for archivo in fila.get('archivos') or []:
                con_backup.setdefault(archivo.get('clave'), archivo)

        # Bases propias de PostgreSQL: no son "sin uso", son del motor.
        sistema = {'postgres', 'rdsadmin', 'azure_maintenance', 'azure_sys'}
        sin_uso_bytes = 0
        for base in resultado.get('bases') or []:
            uso = en_uso.get(base['nombre'])
            base['instancia'] = uso
            base['en_uso'] = bool(uso)
            base['sistema'] = base['nombre'] in sistema
            base['ultimo_backup'] = (con_backup.get(base['nombre']) or {}).get('fecha')
            if not uso and not base['sistema']:
                sin_uso_bytes += base['bytes']
        resultado['sin_uso'] = sum(1 for b in resultado.get('bases') or []
                                   if not b['en_uso'] and not b['sistema'])
        resultado['sin_uso_bytes'] = sin_uso_bytes
        resultado['sin_uso_tamano'] = bytes_legible(sin_uso_bytes)
        return jsonify(resultado)

    @app.route('/api/backups/crear', methods=['POST'])
    @requiere_login
    def api_backups_crear():
        if not _backups_activos():
            return jsonify({'ok': False, 'error': 'Los backups están desactivados'}), 403
        cuerpo = request.get_json(silent=True) or {}
        ids = cuerpo.get('ids') or None
        bases = cuerpo.get('bases') or None
        if bases:
            titulo = 'Backup de la base %s' % ', '.join(bases)
        elif ids:
            titulo = 'Backup de %s' % ', '.join(ids)
        else:
            titulo = 'Backup de todas las instancias'
        tarea = tareas.crear('backup', titulo, {'ids': ids, 'bases': bases},
                             usuario=session.get('usuario') or 'api')
        mod_acciones.registrar_evento(config, tarea.creado_por, 'backup_crear',
                                      ', '.join(bases or ids or ['todas']), 0,
                                      'tarea %s' % tarea.id)
        tareas.lanzar(tarea, lambda t: mod_backups.crear(t, config, colector, ids, bases))
        return jsonify({'ok': True, 'tarea': tarea.id}), 202

    @app.route('/api/backups/eliminar', methods=['POST'])
    @requiere_login
    def api_backups_eliminar():
        if not _backups_activos():
            return jsonify({'ok': False, 'error': 'Los backups están desactivados'}), 403
        cuerpo = request.get_json(silent=True) or {}
        resultado = mod_backups.eliminar(config, cuerpo.get('archivo') or '')
        mod_acciones.registrar_evento(config, session.get('usuario') or 'api', 'backup_eliminar',
                                      cuerpo.get('archivo') or '', 0 if resultado.get('ok') else 1,
                                      resultado.get('error') or '')
        return jsonify(resultado), (200 if resultado.get('ok') else 400)

    @app.route('/backups/descargar')
    @requiere_login
    def descargar_backup():
        archivo = request.args.get('archivo') or ''
        if not _backups_activos() or not mod_backups.ruta_valida(config, archivo):
            return jsonify({'error': 'Archivo no disponible'}), 404
        mod_acciones.registrar_evento(config, session.get('usuario') or 'api',
                                      'backup_descargar', archivo, 0, '')
        return send_file(archivo, as_attachment=True)

    @app.route('/api/diagnostico/vhosts')
    @requiere_login
    def api_diagnostico_vhosts():
        """Qué sitios web leyó el panel y por qué emparejó (o no) cada instancia."""
        from . import webserver as mod_webserver
        vhosts = mod_webserver.cargar_vhosts(config)
        resumen = {
            'directorios_apache': [list(par) for par in mod_webserver.DIRECTORIOS_APACHE],
            'directorios_nginx': [list(par) for par in mod_webserver.DIRECTORIOS_NGINX],
            'apache_dirs_config': config.get('apache_dirs'),
            'nginx_dirs_config': config.get('nginx_dirs'),
            'total': len(vhosts),
            'archivos': [{'archivo': v['archivo'], 'servidor': v.get('servidor'),
                          'servername': v.get('servername'), 'alias': v.get('alias'),
                          'puertos': v.get('puertos_proxy'), 'sockets': v.get('sockets_unix'),
                          'habilitado': v.get('habilitado'), 'ssl': v.get('ssl')}
                         for v in vhosts],
        }
        ident = request.args.get('id')
        if ident:
            todas = discovery.descubrir(config)
            instancias = {i.id: i for i in todas}
            instancia = instancias.get(ident)
            datos = colector.instancia(ident) or {}
            conteo = {}
            for inst in todas:
                dominio = (inst.dominio or '').lower()
                if dominio:
                    conteo[dominio] = conteo.get(dominio, 0) + 1
            if instancia:
                resumen['instancia'] = mod_webserver.diagnostico(
                    instancia, vhosts, datos.get('puerto'), datos.get('socket_ruta'),
                    clientes=[i.cliente for i in todas],
                    dominios_ambiguos=[d for d, n in conteo.items() if n > 1])
        return jsonify(resumen)

    @app.route('/api/instancia/<path:ident>/configuracion', methods=['POST'])
    @requiere_login
    def api_cambiar_configuracion(ident):
        cfg = config.get('acciones') or {}
        if not cfg.get('enabled', True) or not cfg.get('datos', True):
            return jsonify({'ok': False, 'error': 'Los cambios en la base están desactivados'}), 403
        datos = colector.instancia(ident)
        if not datos:
            return jsonify({'ok': False, 'error': 'instancia no encontrada'}), 404

        cuerpo = request.get_json(silent=True) or {}
        campo = (cuerpo.get('campo') or '').strip()
        valor = (cuerpo.get('valor') or '').strip()

        instancias = {i.id: i for i in discovery.descubrir(config)}
        instancia = instancias.get(ident)
        if not instancia:
            return jsonify({'ok': False, 'error': 'no se pudo resolver la instalación'}), 404

        resultado = dbstats.cambiar_configuracion(instancia, config, campo, valor)
        mod_acciones.registrar_evento(
            config, session.get('usuario') or 'api', 'configuracion_%s' % campo,
            datos.get('cliente'), 0 if resultado.get('ok') else 1,
            resultado.get('error') or ('%s = %s' % (campo, valor)))
        if not resultado.get('ok'):
            return jsonify(resultado), 400
        colector.refrescar(forzar=True, solo=ident)
        resultado['instancia'] = colector.instancia(ident)
        return jsonify(resultado)

    @app.route('/api/acciones')
    @requiere_login
    @requiere_ver_excluidos
    def api_historial_acciones():
        """El historial deja ver qué se hizo sobre los sistemas ocultos, así que
        se reserva a quien tiene permiso para verlos."""
        return jsonify({'acciones': mod_acciones.historial(config)})

    @app.route('/api/cron')
    @requiere_login
    def api_cron():
        """Tareas programadas del servidor (crontabs y timers de systemd)."""
        return jsonify(mod_cron.listar(config))

    @app.route('/export.csv', methods=['GET', 'POST'])
    @requiere_login
    def export_csv():
        incluir, solo = ambito_ocultas(request.args.get('ocultas'))
        datos = colector.snapshot(tipo=request.args.get('tipo') or None,
                                  buscar=request.args.get('q') or None,
                                  incluir_ocultas=incluir, solo_ocultas=solo)
        return Response(
            exportar.a_csv(instancias_pedidas(datos)),
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename=instancias.csv'},
        )

    @app.route('/export.xlsx', methods=['GET', 'POST'])
    @requiere_login
    def export_xlsx():
        incluir, solo = ambito_ocultas(request.args.get('ocultas'))
        datos = colector.snapshot(tipo=request.args.get('tipo') or None,
                                  buscar=request.args.get('q') or None,
                                  incluir_ocultas=incluir, solo_ocultas=solo)
        instancias = instancias_pedidas(datos)
        # El resumen del Excel se recalcula sobre lo exportado: si la pantalla
        # está filtrada, los totales de la hoja Resumen son los de ese filtro.
        contenido, error = exportar.a_xlsx(instancias, colector.resumen(instancias))
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
