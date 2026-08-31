# -*- coding: utf-8 -*-
"""Orquestador: recolecta el estado de todas las instancias en paralelo."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import dbstats, discovery, storage, systemd, units, webserver
from .utils import ahora_iso, bytes_legible


def _recolectar_instancia(instancia, config, forzar_media=False, vhosts=None, unidades=None):
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
    datos['servicio_estado'] = systemd.estado_servicio(
        instancia.servicio, timeout=int(config.get('timeout_systemctl') or 10))

    # 2. Apache: el puerto del gunicorn es la señal más confiable para
    #    emparejar el vhost (el dominio de credenciales.json suele estar viejo).
    puerto = (datos['servicio_estado'] or {}).get('puerto') or (unidad or {}).get('puerto')
    datos['puerto'] = puerto
    vhost = webserver.buscar_vhost(instancia, vhosts or [], puerto)
    datos['apache'] = vhost or {'archivo': None, 'habilitado': None,
                                'error': 'No se encontró vhost de Apache para esta instancia'}

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
    apache = datos.get('apache') or {}
    ssl_info = datos.get('ssl') or {}
    url_estado = datos.get('url_estado') or {}
    venta = dbstats.venta_principal(db)
    facturacion = db.get('facturacion') or {}
    auditoria = db.get('auditoria') or {}
    sesiones = db.get('sesiones') or {}
    empresa = db.get('empresa') or {}

    # La base sólo cuenta para la salud si se está consultando.
    bd_cuenta = not db.get('desactivado')
    problemas = (not servicio.get('activo')) or (bd_cuenta and not db.get('ok'))
    if not problemas and ssl_info.get('estado') in ('vencido',):
        problemas = True
    if not servicio.get('activo') and (not bd_cuenta or not db.get('ok')):
        salud = 'error'
    elif problemas or ssl_info.get('estado') == 'por-vencer':
        salud = 'alerta'
    else:
        salud = 'ok'

    return {
        'salud': salud,
        'dominio_desactualizado': bool(datos.get('dominio_desactualizado')),
        'dominio_credenciales': datos.get('dominio_credenciales'),
        'fecha_instalacion': datos.get('fecha_instalacion'),
        'servicio_creado': servicio.get('creado'),
        'servicio_desde': servicio.get('desde'),
        'servicio_uptime': servicio.get('uptime'),
        'apache_sitio': apache.get('sitio'),
        'apache_archivo': apache.get('archivo'),
        'apache_habilitado': apache.get('habilitado'),
        'ssl_estado': ssl_info.get('estado'),
        'ssl_dias': ssl_info.get('dias_restantes'),
        'ssl_hasta': ssl_info.get('valido_hasta'),
        'url_responde': url_estado.get('responde'),
        'url_codigo': url_estado.get('codigo'),
        'empresa': empresa.get('nombre_empresa') or empresa.get('razonsocial') or '',
        'servicio_activo': bool(servicio.get('activo')),
        'servicio_estado': servicio.get('estado'),
        'db_ok': bool(db.get('ok')),
        'db_tamano': db.get('tamano'),
        'db_tamano_bytes': db.get('tamano_bytes'),
        'media_tamano': (datos.get('media') or {}).get('tamano'),
        'media_bytes': (datos.get('media') or {}).get('bytes'),
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
        'ruta': datos.get('ruta'),
        'ultima_sesion': sesiones.get('ultimo_login'),
        'ultima_sesion_usuario': sesiones.get('usuario'),
        'ultima_sesion_dias': sesiones.get('dias'),
        'sesiones_vigentes': sesiones.get('sesiones_vigentes'),
        'venta_tabla': venta.get('etiqueta'),
        'primera_venta': venta.get('primera'),
        'ultima_venta': venta.get('ultima'),
        'ventas_total': venta.get('total'),
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
        self._hilo = None
        self._parar = threading.Event()

    # ---------------------------------------------------------------- lectura
    def snapshot(self, tipo=None, buscar=None):
        with self._lock:
            instancias = [self._datos[i] for i in self._orden if i in self._datos]
            meta = {
                'ultimo_refresco': self._ultimo_refresco,
                'refrescando': self._refrescando,
                'total': len(instancias),
            }
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
        }

    def instancia(self, ident):
        with self._lock:
            return self._datos.get(ident)

    @staticmethod
    def resumen(instancias):
        """Totales para las tarjetas superiores del panel."""
        total = len(instancias)
        servicios_ok = sum(1 for i in instancias if (i.get('resumen') or {}).get('servicio_activo'))
        db_ok = sum(1 for i in instancias if (i.get('resumen') or {}).get('db_ok'))
        db_bytes = sum((i.get('db') or {}).get('tamano_bytes') or 0 for i in instancias)
        media_bytes = sum((i.get('media') or {}).get('bytes') or 0 for i in instancias)
        sitios_ok = sum(1 for i in instancias if (i.get('apache') or {}).get('habilitado'))
        ssl_ok = sum(1 for i in instancias if (i.get('ssl') or {}).get('estado') == 'vigente')
        ssl_alerta = sum(1 for i in instancias
                         if (i.get('ssl') or {}).get('estado') in ('vencido', 'por-vencer'))
        urls_ok = sum(1 for i in instancias if (i.get('url_estado') or {}).get('responde'))
        dominios_viejos = sum(1 for i in instancias if i.get('dominio_desactualizado'))
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
            'db_activas': db_ok,
            'db_caidas': total - db_ok,
            'db_bytes': db_bytes,
            'db_tamano': bytes_legible(db_bytes),
            'media_bytes': media_bytes,
            'media_tamano': bytes_legible(media_bytes),
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
            # Los vhost de Apache se leen una sola vez por refresco.
            vhosts = webserver.cargar_vhosts(self.config)
            unidades = units.cargar_unidades(self.config)
            workers = max(1, int(self.config.get('workers') or 8))
            resultados = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futuros = {
                    pool.submit(self._seguro, inst, forzar_media, vhosts, unidades): inst
                    for inst in instancias
                }
                for futuro, inst in futuros.items():
                    resultados[inst.id] = futuro.result()

            with self._lock:
                if solo:
                    self._datos.update(resultados)
                else:
                    self._datos = resultados
                    self._orden = [i.id for i in instancias]
                self._ultimo_refresco = ahora_iso()
            return True
        finally:
            with self._lock:
                self._refrescando = False

    def _seguro(self, instancia, forzar_media, vhosts=None, unidades=None):
        try:
            return _recolectar_instancia(instancia, self.config, forzar_media,
                                         vhosts, unidades)
        except Exception as ex:  # pragma: no cover - defensivo
            datos = instancia.as_dict()
            datos['error'] = 'Fallo recolectando: %s' % ex
            datos['servicio_estado'] = {'activo': False, 'estado': 'desconocido'}
            datos['db'] = {'ok': False, 'error': str(ex)}
            datos['apache'] = {}
            datos['ssl'] = {}
            datos['url_estado'] = {}
            datos['media'] = {}
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
