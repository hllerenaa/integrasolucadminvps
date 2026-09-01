# -*- coding: utf-8 -*-
"""Orquestador: recolecta el estado de todas las instancias en paralelo."""
from __future__ import annotations

import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import (dbstats, discovery, excluidos as mod_excluidos, logs as mod_logs,
               storage, systemd, units, webserver)
from .utils import ahora_iso, bytes_legible


_RECURSOS = {}
_DISCO = {}


def _recolectar_instancia(instancia, config, forzar_media=False, vhosts=None, unidades=None,
                          servidores_web=None, sockets=None, contexto=None):
    """Estado completo de una instancia (servicio + apache + SSL + base + media)."""
    inicio = time.time()
    datos = instancia.as_dict()

    # 1. Servicio: se busca el .service que apunta a esta carpeta; si no
    #    aparece, se cae al nombre por convención ({cliente}).
    unidad = units.buscar_unidad(instancia, unidades or [], instancia.servicio)
    if unidad:
        instancia.servicio = unidad['unidad']
        datos['servicio'] = unidad['unidad']
        datos['servicio_origen'] = unidad['origen']
        datos['servicio_archivo'] = unidad.get('archivo')
        datos['socket_unix'] = unidad.get('socket_unix')
    datos['servicio_estado'] = systemd.estado_servicio(
        instancia.servicio, timeout=int(config.get('timeout_systemctl') or 10))
    datos.setdefault('servicio_archivo', (datos['servicio_estado'] or {}).get('archivo'))

    # Activación por socket: si el .socket escucha, el sistema responde aunque
    # el .service esté inactivo (systemd lo levanta en la primera petición).
    unidad_socket = units.socket_de(unidad, sockets or {})
    if unidad_socket or (sockets or {}).get(instancia.servicio):
        datos['socket'] = systemd.estado_socket(
            instancia.servicio, timeout=int(config.get('timeout_systemctl') or 10))
        if unidad_socket:
            # El archivo .socket existe aunque systemctl no pueda consultarse.
            datos['socket'].update({'existe': True,
                                    'archivo': unidad_socket.get('archivo'),
                                    'escuchas': unidad_socket.get('escuchas'),
                                    'rutas': unidad_socket.get('rutas'),
                                    'puertos': unidad_socket.get('puertos')})
    else:
        datos['socket'] = {'existe': False}

    # 2. Apache: el puerto del gunicorn es la señal más confiable para
    #    emparejar el vhost (el dominio de credenciales.json suele estar viejo).
    puerto = (datos['servicio_estado'] or {}).get('puerto') or (unidad or {}).get('puerto')
    if not puerto:
        puertos_socket = (datos.get('socket') or {}).get('puertos') or []
        puerto = puertos_socket[0] if puertos_socket else None
    datos['socket_ruta'] = ((datos.get('socket') or {}).get('rutas') or [None])[0] \
        or (unidad or {}).get('socket_unix')
    datos['puerto'] = puerto
    contexto = contexto or {}
    ambiguos = {d.lower() for d in (contexto.get('dominios_ambiguos') or [])}
    datos['dominio_compartido'] = (instancia.dominio or '').lower() in ambiguos
    vhost = webserver.buscar_vhost(instancia, vhosts or [], puerto, datos.get('socket_ruta'),
                                   clientes=contexto.get('clientes'),
                                   dominios_ambiguos=contexto.get('dominios_ambiguos'))
    datos['apache'] = vhost or {'archivo': None, 'habilitado': None,
                                'error': 'No se encontró el sitio web (Apache/nginx) de esta instancia'}
    servidor = (vhost or {}).get('servidor')
    demonio = {'nginx': 'nginx', 'apache': 'apache2'}.get(servidor)
    datos['servidor_web'] = {
        'tipo': servidor,
        'demonio': demonio,
        'activo': (servidores_web or {}).get(demonio, {}).get('activo') if demonio else None,
        'estado': (servidores_web or {}).get(demonio, {}).get('estado') if demonio else None,
    }

    # 3. Dominio real: manda el ServerName del vhost.
    dominios = webserver.dominio_efectivo(instancia, vhost)
    datos.update({
        'dominio': dominios['dominio'],
        'url': dominios['url'],
        'dominio_origen': dominios['origen'],
        'dominio_credenciales': dominios['dominio_credenciales'],
        'dominio_apache': dominios['dominio_apache'],
        'dominio_desactualizado': dominios['desactualizado'],
    })

    datos['ssl'] = webserver.info_certificado(instancia, vhost, dominios['dominio'])

    if config.get('verificar_url', True):
        datos['url_estado'] = webserver.verificar_url(
            dominios['url'], timeout=int(config.get('timeout_url') or 6))
    else:
        datos['url_estado'] = {'url': dominios['url'], 'responde': None,
                               'error': 'verificación desactivada'}

    if config.get('consultar_bd', True):
        datos['db'] = dbstats.consultar(instancia, config)
    else:
        datos['db'] = {'ok': None, 'desactivado': True,
                       'dbname': (instancia.credenciales or {}).get('POSTGRES_DBNAME')}

    datos['media'] = (storage.tamano_media(instancia, config, forzar=forzar_media)
                      if config.get('medir_media', True) else {})
    datos['logs'] = (mod_logs.logs_de_instancia(instancia, vhost, unidad)
                     if config.get('medir_logs', True) else {})
    datos['git'] = storage.info_git(instancia.ruta)
    datos['fecha_instalacion'] = storage.fecha_instalacion(instancia.ruta)
    datos['actualizado'] = ahora_iso()
    datos['duracion_ms'] = int((time.time() - inicio) * 1000)
    datos['resumen'] = _resumen_fila(datos)
    return datos


def _resumen_fila(datos):
    """Campos planos para la tabla del panel."""
    db = datos.get('db') or {}
    servicio = datos.get('servicio_estado') or {}
    socket_info = datos.get('socket') or {}
    apache = datos.get('apache') or {}
    ssl_info = datos.get('ssl') or {}
    url_estado = datos.get('url_estado') or {}
    venta = dbstats.venta_principal(db)
    facturacion = db.get('facturacion') or {}
    api_cedula = db.get('api_cedula') or {}
    auditoria = db.get('auditoria') or {}
    sesiones = db.get('sesiones') or {}
    empresa = db.get('empresa') or {}

    # La base sólo cuenta para la salud si se está consultando.
    bd_cuenta = not db.get('desactivado')
    # Con activación por socket el sistema atiende aunque el .service esté parado.
    atiende = bool(servicio.get('activo') or socket_info.get('activo'))
    problemas = (not atiende) or (bd_cuenta and not db.get('ok'))
    if not problemas and ssl_info.get('estado') in ('vencido',):
        problemas = True
    if not atiende and (not bd_cuenta or not db.get('ok')):
        salud = 'error'
    elif problemas or ssl_info.get('estado') == 'por-vencer':
        salud = 'alerta'
    else:
        salud = 'ok'

    ram_total = (_RECURSOS.get('ram_total') or 0)
    memoria = servicio.get('memoria_bytes') or 0
    disco_total = (_DISCO.get('total_bytes') or 0)
    db_bytes = db.get('tamano_bytes') or 0
    media_bytes = (datos.get('media') or {}).get('bytes') or 0
    logs_bytes = (datos.get('logs') or {}).get('bytes') or 0

    # "Sin uso": días desde la última señal de actividad real (auditoría,
    # inicio de sesión o venta). Se toma la más reciente de las tres.
    senales = [auditoria.get('dias'), sesiones.get('dias'), venta.get('dias_sin_ventas')]
    senales = [d for d in senales if isinstance(d, int) and d >= 0]
    dias_sin_uso = min(senales) if senales else None

    return {
        'salud': salud,
        'dias_sin_uso': dias_sin_uso,
        'cpu_pct': servicio.get('cpu_pct'),
        'ram_bytes': servicio.get('memoria_bytes'),
        'ram_legible': servicio.get('memoria'),
        'ram_pct': round(memoria * 100.0 / ram_total, 2) if (ram_total and memoria) else None,
        'db_pct_disco': round(db_bytes * 100.0 / disco_total, 2) if (disco_total and db_bytes) else None,
        'media_pct_disco': round(media_bytes * 100.0 / disco_total, 2) if (disco_total and media_bytes) else None,
        'ocupa_bytes': db_bytes + media_bytes + logs_bytes,
        'ocupa_legible': bytes_legible(db_bytes + media_bytes + logs_bytes),
        'ocupa_pct_disco': (round((db_bytes + media_bytes + logs_bytes) * 100.0 / disco_total, 2)
                            if disco_total else None),
        'dominio_desactualizado': bool(datos.get('dominio_desactualizado')),
        'dominio_compartido': bool(datos.get('dominio_compartido')),
        'dominio_credenciales': datos.get('dominio_credenciales'),
        'fecha_instalacion': datos.get('fecha_instalacion'),
        'servicio_creado': servicio.get('creado'),
        'servicio_desde': servicio.get('desde'),
        'servicio_uptime': servicio.get('uptime'),
        'apache_sitio': apache.get('sitio'),
        'apache_archivo': apache.get('archivo'),
        'apache_habilitado': apache.get('habilitado'),
        'servidor_web': (datos.get('servidor_web') or {}).get('tipo'),
        'servidor_web_activo': (datos.get('servidor_web') or {}).get('activo'),
        'servidor_web_demonio': (datos.get('servidor_web') or {}).get('demonio'),
        'ssl_estado': ssl_info.get('estado'),
        'ssl_dias': ssl_info.get('dias_restantes'),
        'ssl_hasta': ssl_info.get('valido_hasta'),
        'ssl_emisor': ssl_info.get('emisor'),
        'url_responde': url_estado.get('responde'),
        'url_codigo': url_estado.get('codigo'),
        'empresa': empresa.get('nombre_empresa') or empresa.get('razonsocial') or '',
        'ruc': empresa.get('ruc'),
        'ruc_proveedor': empresa.get('rucproveedor'),
        'ruc_proveedor_disponible': bool(empresa.get('_rucproveedor_disponible')),
        'servicio_activo': bool(servicio.get('activo')),
        'servicio_estado': servicio.get('estado'),
        'socket_existe': bool(socket_info.get('existe')),
        'socket_activo': bool(socket_info.get('activo')),
        'atiende': bool(servicio.get('activo') or socket_info.get('activo')),
        'db_ok': bool(db.get('ok')),
        'db_tamano': db.get('tamano'),
        'db_tamano_bytes': db.get('tamano_bytes'),
        'media_tamano': (datos.get('media') or {}).get('tamano'),
        'media_bytes': (datos.get('media') or {}).get('bytes'),
        'logs_tamano': (datos.get('logs') or {}).get('tamano'),
        'logs_bytes': (datos.get('logs') or {}).get('bytes'),
        'logs_archivos': (datos.get('logs') or {}).get('total_archivos'),
        'auditoria_fecha': auditoria.get('fecha'),
        'auditoria_hora': auditoria.get('hora'),
        'auditoria_usuario': auditoria.get('usuario'),
        'auditoria_accion': auditoria.get('accion'),
        'auditoria_tabla': auditoria.get('tabla_afectada'),
        'auditoria_dias': auditoria.get('dias'),
        'facturas_total': facturacion.get('total'),
        'facturas_mes': facturacion.get('mes_actual'),
        'facturas_mes_anterior': facturacion.get('mes_anterior'),
        'facturas_ultimo_mes': facturacion.get('ultimo_mes'),
        'facturas_meses_sin': facturacion.get('meses_sin_facturar'),
        'facturas_estado': facturacion.get('estado'),
        'api_cedula': api_cedula.get('activa'),
        'api_cedula_disponible': bool(api_cedula.get('disponible')),
        'api_cedula_columna': api_cedula.get('columna'),
        'ruta': datos.get('ruta'),
        'ultima_sesion': sesiones.get('ultimo_login'),
        'ultima_sesion_usuario': sesiones.get('usuario'),
        'ultima_sesion_dias': sesiones.get('dias'),
        'sesiones_vigentes': sesiones.get('sesiones_vigentes'),
        'venta_tabla': venta.get('etiqueta'),
        'primera_venta': venta.get('primera'),
        'ultima_venta': venta.get('ultima'),
        'ventas_total': venta.get('total'),
        'ventas_anio': venta.get('anio_actual'),
        'ventas_anio_anterior': venta.get('anio_anterior'),
        'facturas_anio': next((a['total'] for a in (facturacion.get('por_anio') or [])
                               if a.get('anio') == datetime.date.today().year), 0),
        'dias_sin_ventas': venta.get('dias_sin_ventas'),
    }


class Colector(object):
    """Mantiene en memoria el último estado conocido de cada instancia."""

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._datos = {}          # id -> dict
        self._orden = []          # ids en orden de descubrimiento
        self._ultimo_refresco = None
        self._refrescando = False
        self._ocultas = 0
        self._servidores_web = {}
        self._pendientes = 0
        self._recolectadas = 0
        self._marcadas = {}
        self._hilo = None
        self._parar = threading.Event()

    # ---------------------------------------------------------------- lectura
    def snapshot(self, tipo=None, buscar=None, incluir_ocultas=False, solo_ocultas=False):
        with self._lock:
            instancias = [self._datos[i] for i in self._orden if i in self._datos]
            meta = {
                'ultimo_refresco': self._ultimo_refresco,
                'refrescando': self._refrescando,
                'total': len(instancias),
                'ocultas': self._ocultas,
                'recolectar_ocultas': bool(self.config.get('recolectar_ocultas', True)),
                'pendientes': max(0, self._pendientes - self._recolectadas),
                'recolectadas': self._recolectadas,
                'esperadas': self._pendientes,
            }
        if solo_ocultas:
            instancias = [i for i in instancias if i.get('oculta')]
        elif not incluir_ocultas:
            instancias = [i for i in instancias if not i.get('oculta')]
        if tipo:
            instancias = [i for i in instancias if i.get('tipo') == tipo]
        if buscar:
            texto = buscar.lower()
            instancias = [i for i in instancias
                          if texto in (i.get('cliente') or '').lower()
                          or texto in ((i.get('resumen') or {}).get('empresa') or '').lower()
                          or texto in (i.get('dominio') or '').lower()]
        return {
            'meta': meta,
            'resumen': self.resumen(instancias),
            'instancias': instancias,
            'disco': storage.uso_disco('/'),
            'servidores_web': dict(self._servidores_web),
            'recursos': systemd.recursos_del_sistema(),
        }

    def remarcar_ocultas(self):
        """Recalcula la marca de ocultas sobre lo ya recolectado.

        Permite que el interruptor de /excluidos se refleje al instante, sin
        esperar a que termine un refresco completo.
        """
        ocultos = mod_excluidos.cargar(self.config)
        with self._lock:
            total = 0
            for datos in self._datos.values():
                oculta = mod_excluidos.excluida(ocultos, datos.get('cliente'),
                                                datos.get('servicio'))
                datos['oculta'] = oculta
                self._marcadas[datos.get('id')] = oculta
                total += 1 if oculta else 0
            self._ocultas = total
        return total

    def instancia(self, ident):
        with self._lock:
            return self._datos.get(ident)

    @staticmethod
    def resumen(instancias):
        """Totales para las tarjetas superiores del panel."""
        total = len(instancias)
        servicios_ok = sum(1 for i in instancias if (i.get('resumen') or {}).get('atiende'))
        db_ok = sum(1 for i in instancias if (i.get('resumen') or {}).get('db_ok'))
        db_bytes = sum((i.get('db') or {}).get('tamano_bytes') or 0 for i in instancias)
        media_bytes = sum((i.get('media') or {}).get('bytes') or 0 for i in instancias)
        logs_bytes = sum((i.get('logs') or {}).get('bytes') or 0 for i in instancias)
        ram_bytes = sum((i.get('servicio_estado') or {}).get('memoria_bytes') or 0 for i in instancias)
        cpu_pct = sum((i.get('resumen') or {}).get('cpu_pct') or 0 for i in instancias)
        sitios_ok = sum(1 for i in instancias if (i.get('apache') or {}).get('habilitado'))
        ssl_ok = sum(1 for i in instancias if (i.get('ssl') or {}).get('estado') == 'vigente')
        ssl_alerta = sum(1 for i in instancias
                         if (i.get('ssl') or {}).get('estado') in ('vencido', 'por-vencer'))
        urls_ok = sum(1 for i in instancias if (i.get('url_estado') or {}).get('responde'))
        dominios_viejos = sum(1 for i in instancias if i.get('dominio_desactualizado'))
        dominios_compartidos = sum(1 for i in instancias if i.get('dominio_compartido'))
        sin_uso_90 = sum(1 for i in instancias
                         if isinstance((i.get('resumen') or {}).get('dias_sin_uso'), int)
                         and (i.get('resumen') or {}).get('dias_sin_uso') > 90)
        sin_ruc_proveedor = sum(1 for i in instancias
                                if (i.get('resumen') or {}).get('ruc_proveedor_disponible')
                                and not (i.get('resumen') or {}).get('ruc_proveedor'))
        sin_columna_ruc = sum(1 for i in instancias
                              if (i.get('db') or {}).get('ok')
                              and not (i.get('resumen') or {}).get('ruc_proveedor_disponible'))
        api_activas = sum(1 for i in instancias if (i.get('resumen') or {}).get('api_cedula'))
        api_inactivas = sum(1 for i in instancias
                            if (i.get('resumen') or {}).get('api_cedula') is False)
        por_tipo = {}
        for i in instancias:
            por_tipo[i.get('tipo')] = por_tipo.get(i.get('tipo'), 0) + 1
        return {
            'total': total,
            'servicios_activos': servicios_ok,
            'servicios_inactivos': total - servicios_ok,
            'sitios_habilitados': sitios_ok,
            'ssl_vigentes': ssl_ok,
            'ssl_alerta': ssl_alerta,
            'urls_ok': urls_ok,
            'dominios_desactualizados': dominios_viejos,
            'dominios_compartidos': dominios_compartidos,
            'sin_uso_90': sin_uso_90,
            'sin_ruc_proveedor': sin_ruc_proveedor,
            'sin_columna_ruc_proveedor': sin_columna_ruc,
            'api_cedula_activas': api_activas,
            'api_cedula_inactivas': api_inactivas,
            'db_activas': db_ok,
            'db_caidas': total - db_ok,
            'db_bytes': db_bytes,
            'db_tamano': bytes_legible(db_bytes),
            'media_bytes': media_bytes,
            'media_tamano': bytes_legible(media_bytes),
            'logs_bytes': logs_bytes,
            'logs_tamano': bytes_legible(logs_bytes),
            'ram_bytes': ram_bytes,
            'ram_tamano': bytes_legible(ram_bytes),
            'cpu_pct': round(cpu_pct, 1),
            'ocupa_bytes': media_bytes + logs_bytes + db_bytes,
            'ocupa_tamano': bytes_legible(media_bytes + logs_bytes + db_bytes),
            'por_tipo': por_tipo,
        }

    # ------------------------------------------------------------- recolección
    def refrescar(self, forzar=False, forzar_media=False, solo=None):
        """Vuelve a consultar todas las instancias (o sólo una)."""
        with self._lock:
            if self._refrescando and not forzar:
                return False
            self._refrescando = True
        try:
            instancias = discovery.descubrir(self.config)
            if solo:
                instancias = [i for i in instancias if i.id == solo]
            # Los vhost y las unidades se leen una sola vez por refresco.
            vhosts = webserver.cargar_vhosts(self.config)
            unidades = units.cargar_unidades(self.config)
            self._servidores_web = webserver.estado_servidores_web(self.config)
            sockets = units.cargar_sockets(self.config)
            # Contexto para no asignarle a una instancia el vhost de otra:
            # todos los clientes y los dominios que aparecen repetidos en
            # credenciales.json (típicamente el del template).
            todos = discovery.descubrir(self.config)
            conteo = {}
            for inst in todos:
                dominio = (inst.dominio or '').lower()
                if dominio:
                    conteo[dominio] = conteo.get(dominio, 0) + 1
            contexto = {
                'clientes': [i.cliente for i in todos],
                'dominios_ambiguos': [d for d, n in conteo.items() if n > 1],
            }
            # Totales del servidor, para poder expresar el consumo en porcentaje.
            _RECURSOS.clear()
            _RECURSOS.update(systemd.recursos_del_sistema())
            _DISCO.clear()
            _DISCO.update(storage.uso_disco('/'))

            # excluidos.txt puede nombrar el cliente o su servicio systemd, así
            # que se resuelve con la unidad ya identificada. Las ocultas se
            # recolectan igual (la página /excluidos muestra sus datos y permite
            # actuar sobre ellas); sólo quedan marcadas para no salir en el panel.
            ocultos = mod_excluidos.cargar(self.config)
            recolectar_ocultas = self.config.get('recolectar_ocultas', True)
            marcadas, visibles, omitidas = {}, [], 0
            for inst in instancias:
                unidad = units.buscar_unidad(inst, unidades, inst.servicio)
                nombre_servicio = (unidad or {}).get('unidad') or inst.servicio
                oculta = mod_excluidos.excluida(ocultos, inst.cliente, nombre_servicio)
                if oculta:
                    omitidas += 1
                    if not recolectar_ocultas:
                        continue
                marcadas[inst.id] = oculta
                visibles.append(inst)
            instancias = visibles
            self._ocultas = omitidas
            self._marcadas = marcadas
            workers = max(1, int(self.config.get('workers') or 8))
            with self._lock:
                if not solo:
                    # El orden se fija de una vez para que las filas aparezcan
                    # en su sitio conforme se van recolectando.
                    self._orden = [i.id for i in instancias]
                    self._datos = {k: v for k, v in self._datos.items()
                                   if k in set(self._orden)}
                self._pendientes = len(instancias)
                self._recolectadas = 0

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futuros = {
                    pool.submit(self._seguro, inst, forzar_media, vhosts, unidades,
                                self._servidores_web, sockets, contexto): inst
                    for inst in instancias
                }
                for futuro in as_completed(futuros):
                    inst = futuros[futuro]
                    try:
                        datos = futuro.result()
                    except Exception as ex:  # pragma: no cover - defensivo
                        datos = inst.as_dict()
                        datos['error'] = 'Fallo recolectando: %s' % ex
                        datos['servicio_estado'] = {'activo': False, 'estado': 'desconocido'}
                        datos['db'] = {'ok': False, 'error': str(ex)}
                        for clave in ('apache', 'ssl', 'url_estado', 'media', 'logs',
                                      'git', 'socket'):
                            datos.setdefault(clave, {})
                        datos['actualizado'] = ahora_iso()
                        datos['resumen'] = _resumen_fila(datos)
                    # Cada instancia se publica apenas está lista: el panel se
                    # va llenando en vez de quedarse vacío hasta el final.
                    datos['oculta'] = bool(self._marcadas.get(inst.id))
                    with self._lock:
                        self._datos[inst.id] = datos
                        self._recolectadas += 1

            with self._lock:
                self._ultimo_refresco = ahora_iso()
                self._pendientes = 0
            return True
        finally:
            with self._lock:
                self._refrescando = False

    def _seguro(self, instancia, forzar_media, vhosts=None, unidades=None,
                servidores_web=None, sockets=None, contexto=None):
        try:
            return _recolectar_instancia(instancia, self.config, forzar_media,
                                         vhosts, unidades, servidores_web, sockets, contexto)
        except Exception as ex:  # pragma: no cover - defensivo
            datos = instancia.as_dict()
            datos['error'] = 'Fallo recolectando: %s' % ex
            datos['servicio_estado'] = {'activo': False, 'estado': 'desconocido'}
            datos['db'] = {'ok': False, 'error': str(ex)}
            datos['apache'] = {}
            datos['ssl'] = {}
            datos['url_estado'] = {}
            datos['media'] = {}
            datos['logs'] = {}
            datos['git'] = {}
            datos['actualizado'] = ahora_iso()
            datos['resumen'] = _resumen_fila(datos)
            return datos

    # ------------------------------------------------------------------- hilo
    def iniciar_en_segundo_plano(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name='colector', daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()

    def _bucle(self):
        intervalo = max(30, int(self.config.get('intervalo_refresco') or 300))
        while not self._parar.is_set():
            try:
                self.refrescar()
            except Exception:
                pass
            self._parar.wait(intervalo)
