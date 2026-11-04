# -*- coding: utf-8 -*-
"""Alertas y notificaciones: Telegram, correo, push al celular y la campana del panel.

Un hilo revisa el estado cada `notificaciones.intervalo` segundos y compara
con la vuelta anterior:

- Una alerta NUEVA (que se confirma en `confirmaciones` vueltas seguidas, para
  no avisar por un reinicio de segundos) se envía por todos los canales.
- Si SIGUE activa, se recuerda cada `repetir_horas`.
- Cuando se RESUELVE se avisa que volvió a la normalidad.

Todo lo nuevo de una vuelta sale en un solo mensaje por canal: si se cae
Apache no llegan 40 mensajes, llega uno con las 40 instancias.

Además se notifica el final de las tareas largas (backups, altas, certbot).
El historial (la campana) se guarda en var/notificaciones.json.
"""
from __future__ import annotations

import copy
import datetime
import html
import json
import os
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import webpush
from .utils import ahora_iso

MAX_HISTORIAL = 300

DEFAULTS = {
    'enabled': True,
    'intervalo': 300,
    'confirmaciones': 2,
    'repetir_horas': 6,
    'avisar_recuperacion': True,
    'url_panel': '',
    'telegram': {'enabled': False, 'bot_token': '', 'chat_ids': []},
    'correo': {'enabled': False, 'servidor': '', 'puerto': 587, 'seguridad': 'starttls',
               'usuario': '', 'clave': '', 'remitente': '', 'destinatarios': []},
    'push': {'enabled': True, 'sujeto': ''},
    'reglas': {
        'servicio_caido': True,
        'url_caida': True,
        'base_caida': True,
        'servidor_web_caido': True,
        'ssl_dias': 15,
        'disco_pct': 85,
        'ram_pct': 92,
        'backup_atrasado': False,
        'cobro_vencido': True,
        'renovacion_automatica': True,
        'tareas': True,
    },
}

# Campos que nunca se devuelven al navegador tal cual.
SECRETOS = (('telegram', 'bot_token'), ('correo', 'clave'))
MASCARA = '••••••••'


def _mezclar(base, extra):
    salida = copy.deepcopy(base)
    for clave, valor in (extra or {}).items():
        if isinstance(valor, dict) and isinstance(salida.get(clave), dict):
            salida[clave] = _mezclar(salida[clave], valor)
        else:
            salida[clave] = valor
    return salida


def _lista(valor):
    if isinstance(valor, str):
        valor = valor.replace(';', ',').replace('\n', ',').split(',')
    return [str(v).strip() for v in (valor or []) if str(v).strip()]


class Notificador(object):
    def __init__(self, config, colector, cobros=None):
        self.config = config
        self.colector = colector
        self.cobros = cobros
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        self._historial = None
        self._estado = None
        self._suscripciones = None
        self.url_detectada = ''
        self.ultima_revision = None
        self._cache_alertas = None       # (momento, lista) para el modal del panel
        self._vistas = {}                # clave -> desde cuándo está activa
        self._vence_cert = {}            # ruta -> (mtime, vencimiento)

    # ------------------------------------------------------------ configuración
    def cfg(self):
        return _mezclar(DEFAULTS, self.config.get('notificaciones') or {})

    def cfg_publica(self):
        """La configuración para el formulario, sin revelar tokens ni claves."""
        datos = self.cfg()
        for seccion, campo in SECRETOS:
            valor = datos.get(seccion, {}).get(campo)
            datos[seccion][campo] = MASCARA if valor else ''
        datos['push']['disponible'] = webpush.disponible()
        datos['url_detectada'] = self.url_detectada
        return datos

    def guardar_cfg(self, nuevos):
        """Guarda la configuración en config.json (sección «notificaciones»)."""
        actual = self.cfg()
        nuevos = _mezclar(actual, nuevos or {})
        # Una máscara o un campo vacío en un secreto significa «no cambiarlo».
        for seccion, campo in SECRETOS:
            valor = (nuevos.get(seccion) or {}).get(campo)
            if not valor or valor == MASCARA:
                nuevos[seccion][campo] = actual[seccion][campo]
        nuevos['telegram']['chat_ids'] = _lista(nuevos['telegram'].get('chat_ids'))
        nuevos['correo']['destinatarios'] = _lista(nuevos['correo'].get('destinatarios'))
        for campo, minimo in (('intervalo', 60), ('confirmaciones', 1), ('repetir_horas', 0)):
            try:
                nuevos[campo] = max(minimo, int(nuevos.get(campo) or 0))
            except (TypeError, ValueError):
                nuevos[campo] = DEFAULTS[campo]
        try:
            nuevos['correo']['puerto'] = int(nuevos['correo'].get('puerto') or 587)
        except (TypeError, ValueError):
            nuevos['correo']['puerto'] = 587
        for campo in ('ssl_dias', 'disco_pct', 'ram_pct'):
            try:
                nuevos['reglas'][campo] = int(nuevos['reglas'].get(campo) or 0)
            except (TypeError, ValueError):
                nuevos['reglas'][campo] = DEFAULTS['reglas'][campo]
        nuevos.pop('url_detectada', None)
        nuevos['push'].pop('disponible', None)

        ruta = self.config.path
        datos = {}
        if ruta and os.path.isfile(ruta) and not ruta.endswith('config.example.json'):
            with open(ruta, 'r', encoding='utf-8') as fh:
                datos = json.load(fh)
        elif ruta and ruta.endswith('config.example.json'):
            ruta = os.path.join(os.path.dirname(ruta), 'config.json')
        datos['notificaciones'] = nuevos
        tmp = ruta + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(datos, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, ruta)
        self.config['notificaciones'] = nuevos
        return self.cfg_publica()

    # ------------------------------------------------------------ persistencia
    def _ruta(self, nombre):
        return os.path.join(self.config.var_dir, nombre)

    def _leer(self, nombre, defecto):
        try:
            with open(self._ruta(nombre), 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            return defecto

    def _escribir(self, nombre, datos):
        ruta = self._ruta(nombre)
        try:
            with open(ruta + '.tmp', 'w', encoding='utf-8') as fh:
                json.dump(datos, fh, ensure_ascii=False)
            os.replace(ruta + '.tmp', ruta)
        except OSError:
            pass

    def _cargar(self):
        if self._historial is None:
            self._historial = self._leer('notificaciones.json', [])
            self._estado = self._leer('alertas_estado.json', {})
            self._suscripciones = self._leer('push_suscripciones.json', [])

    # --------------------------------------------------------------- historial
    def historial(self, limite=100):
        with self._lock:
            self._cargar()
            lista = list(reversed(self._historial[-limite:]))
            no_leidas = sum(1 for n in self._historial if not n.get('leida'))
            activas = [dict(v, clave=k) for k, v in self._estado.items() if v.get('notificada')]
        activas.sort(key=lambda a: a.get('desde') or '')
        return {'notificaciones': lista, 'no_leidas': no_leidas, 'activas': activas,
                'ultima_revision': self.ultima_revision}

    def marcar_leidas(self, ids=None):
        with self._lock:
            self._cargar()
            for n in self._historial:
                if ids is None or n.get('id') in ids:
                    n['leida'] = True
            self._escribir('notificaciones.json', self._historial)

    def borrar_historial(self):
        with self._lock:
            self._historial = []
            self._escribir('notificaciones.json', [])

    def _agregar(self, entradas):
        with self._lock:
            self._cargar()
            self._historial.extend(entradas)
            self._historial = self._historial[-MAX_HISTORIAL:]
            self._escribir('notificaciones.json', self._historial)

    # ------------------------------------------------------------ suscripciones
    def suscribir(self, suscripcion, usuario, agente=''):
        endpoint = (suscripcion or {}).get('endpoint')
        if not endpoint or not (suscripcion.get('keys') or {}).get('p256dh'):
            return False
        with self._lock:
            self._cargar()
            self._suscripciones = [s for s in self._suscripciones if s.get('endpoint') != endpoint]
            self._suscripciones.append({'endpoint': endpoint, 'keys': suscripcion['keys'],
                                        'usuario': usuario, 'agente': (agente or '')[:200],
                                        'desde': ahora_iso()})
            self._escribir('push_suscripciones.json', self._suscripciones)
        return True

    def desuscribir(self, endpoint):
        with self._lock:
            self._cargar()
            antes = len(self._suscripciones)
            self._suscripciones = [s for s in self._suscripciones if s.get('endpoint') != endpoint]
            self._escribir('push_suscripciones.json', self._suscripciones)
            return antes != len(self._suscripciones)

    def dispositivos(self):
        with self._lock:
            self._cargar()
            return [{'endpoint': s['endpoint'], 'usuario': s.get('usuario'),
                     'agente': s.get('agente'), 'desde': s.get('desde'),
                     'servicio': s['endpoint'].split('/')[2]} for s in self._suscripciones]

    # ------------------------------------------------------------------ envío
    def _url(self, ruta='/'):
        base = (self.cfg().get('url_panel') or self.url_detectada or '').rstrip('/')
        return (base + ruta) if base else ''

    def publicar(self, titulo, mensaje, nivel='info', ruta='/', canales=None, entradas=None,
                 esperar=False):
        """Registra en la campana y envía por los canales activos.

        `entradas` permite guardar varias líneas del historial (una por
        alerta) y enviar un solo mensaje resumido.
        """
        cfg = self.cfg()
        momento = ahora_iso()
        if entradas is None:
            entradas = [{'titulo': titulo, 'mensaje': mensaje, 'nivel': nivel, 'ruta': ruta}]
        registros = [dict(e, id=uuid.uuid4().hex[:12], fecha=momento, leida=False)
                     for e in entradas]
        self._agregar(registros)

        if not cfg.get('enabled', True):
            return {}

        def enviar():
            resultados = {}
            for canal, funcion in (('telegram', self._telegram), ('correo', self._correo),
                                   ('push', self._push)):
                if canales and canal not in canales:
                    continue
                if not (cfg.get(canal) or {}).get('enabled'):
                    continue
                try:
                    resultados[canal] = funcion(cfg, titulo, mensaje, nivel, ruta)
                except Exception as ex:   # un canal roto no frena a los demás
                    resultados[canal] = {'ok': False, 'error': str(ex)}
            return resultados

        if esperar:
            return enviar()
        threading.Thread(target=enviar, name='notificar', daemon=True).start()
        return {}

    def _telegram(self, cfg, titulo, mensaje, nivel, ruta):
        tg = cfg['telegram']
        if not tg.get('bot_token') or not tg.get('chat_ids'):
            return {'ok': False, 'error': 'Falta el token del bot o el chat_id'}
        icono = {'error': '🔴', 'aviso': '🟠', 'ok': '🟢'}.get(nivel, '🔵')
        texto = '%s <b>%s</b>\n%s' % (icono, html.escape(titulo), html.escape(mensaje))
        enlace = self._url(ruta)
        if enlace:
            texto += '\n\n<a href="%s">Abrir el panel</a>' % html.escape(enlace)
        errores = []
        for chat in tg['chat_ids']:
            cuerpo = json.dumps({'chat_id': chat, 'text': texto[:4000], 'parse_mode': 'HTML',
                                 'disable_web_page_preview': True}).encode('utf-8')
            peticion = urllib.request.Request(
                'https://api.telegram.org/bot%s/sendMessage' % tg['bot_token'], data=cuerpo,
                headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(peticion, timeout=15) as respuesta:
                    respuesta.read()
            except urllib.error.HTTPError as ex:
                try:
                    detalle = json.loads(ex.read().decode('utf-8')).get('description')
                except Exception:
                    detalle = str(ex)
                errores.append('%s: %s' % (chat, detalle))
            except Exception as ex:
                errores.append('%s: %s' % (chat, ex))
        return {'ok': not errores, 'error': '; '.join(errores) or None,
                'enviados': len(tg['chat_ids']) - len(errores)}

    def _correo(self, cfg, titulo, mensaje, nivel, ruta):
        c = cfg['correo']
        if not c.get('servidor') or not c.get('destinatarios'):
            return {'ok': False, 'error': 'Falta el servidor SMTP o los destinatarios'}
        remitente = c.get('remitente') or c.get('usuario')
        correo = EmailMessage()
        correo['Subject'] = '[%s] %s' % (self.config.get('titulo') or 'Panel VPS', titulo)
        correo['From'] = remitente
        correo['To'] = ', '.join(c['destinatarios'])
        correo['Date'] = formatdate(localtime=True)
        correo['Message-ID'] = make_msgid()
        enlace = self._url(ruta)
        correo.set_content(mensaje + ('\n\nAbrir el panel: %s' % enlace if enlace else '')
                           + '\n\n— Enviado por el panel de administración del VPS')
        seguridad = (c.get('seguridad') or 'starttls').lower()
        puerto = int(c.get('puerto') or (465 if seguridad == 'ssl' else 587))
        contexto = ssl.create_default_context()
        if seguridad == 'ssl':
            servidor = smtplib.SMTP_SSL(c['servidor'], puerto, timeout=20, context=contexto)
        else:
            servidor = smtplib.SMTP(c['servidor'], puerto, timeout=20)
        try:
            servidor.ehlo()
            if seguridad == 'starttls':
                servidor.starttls(context=contexto)
                servidor.ehlo()
            if c.get('usuario'):
                servidor.login(c['usuario'], c.get('clave') or '')
            servidor.send_message(correo)
        finally:
            try:
                servidor.quit()
            except Exception:
                pass
        return {'ok': True, 'enviados': len(c['destinatarios'])}

    def _push(self, cfg, titulo, mensaje, nivel, ruta):
        if not webpush.disponible():
            return {'ok': False, 'error': 'Falta la librería cryptography'}
        with self._lock:
            self._cargar()
            suscripciones = list(self._suscripciones)
        if not suscripciones:
            return {'ok': False, 'error': 'Ningún dispositivo activó las notificaciones'}
        sujeto = (cfg['push'].get('sujeto') or '').strip()
        if not sujeto:
            destinatarios = cfg['correo'].get('destinatarios') or []
            sujeto = self._url('/') or ('mailto:%s' % destinatarios[0] if destinatarios
                                        else 'mailto:admin@localhost')
        elif '@' in sujeto and not sujeto.startswith('mailto:'):
            sujeto = 'mailto:' + sujeto
        contenido = {'title': titulo, 'body': mensaje[:600], 'url': ruta or '/', 'nivel': nivel,
                     'tag': 'panel-%s' % nivel, 'fecha': ahora_iso()}
        enviados, errores, caducadas = 0, [], []
        for sus in suscripciones:
            ok, codigo, detalle = webpush.enviar(self.config, sus, contenido, sujeto=sujeto,
                                                 urgencia='high' if nivel == 'error' else 'normal')
            if ok:
                enviados += 1
            elif codigo in (404, 410):
                caducadas.append(sus['endpoint'])
            else:
                errores.append('%s: %s' % (codigo or '-', detalle))
        for endpoint in caducadas:
            self.desuscribir(endpoint)
        return {'ok': enviados > 0, 'enviados': enviados, 'caducadas': len(caducadas),
                'error': '; '.join(errores[:3]) or None}

    def probar(self, canal):
        """Envía un mensaje de prueba por un canal y devuelve el resultado."""
        cfg = self.cfg()
        funciones = {'telegram': self._telegram, 'correo': self._correo, 'push': self._push}
        if canal not in funciones:
            return {'ok': False, 'error': 'Canal desconocido'}
        try:
            resultado = funciones[canal](cfg, 'Prueba de notificación',
                                         'Si ves esto, el canal %s funciona.' % canal, 'ok', '/')
        except Exception as ex:
            resultado = {'ok': False, 'error': str(ex)}
        return resultado

    def detectar_chats(self):
        """Chats que escribieron al bot (para no tener que buscar el chat_id a mano)."""
        token = self.cfg()['telegram'].get('bot_token')
        if not token:
            return {'ok': False, 'error': 'Primero guarda el token del bot'}
        try:
            with urllib.request.urlopen('https://api.telegram.org/bot%s/getUpdates' % token,
                                        timeout=15) as respuesta:
                datos = json.loads(respuesta.read().decode('utf-8'))
        except urllib.error.HTTPError as ex:
            return {'ok': False, 'error': 'Telegram respondió %s: revisa el token' % ex.code}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}
        chats = {}
        for actualizacion in datos.get('result') or []:
            mensaje = (actualizacion.get('message') or actualizacion.get('channel_post')
                       or actualizacion.get('my_chat_member') or {})
            chat = mensaje.get('chat') or {}
            if chat.get('id') is not None:
                nombre = (chat.get('title') or ' '.join(filter(None, [chat.get('first_name'),
                                                                      chat.get('last_name')]))
                          or chat.get('username') or '')
                chats[str(chat['id'])] = {'id': str(chat['id']), 'nombre': nombre,
                                          'tipo': chat.get('type')}
        return {'ok': True, 'chats': list(chats.values())}

    # ------------------------------------------------------------------ alertas
    def evaluar(self):
        """Alertas activas en este momento: {clave: {titulo, mensaje, nivel, ruta}}."""
        from . import backups as mod_backups, certificados, consumo

        reglas = self.cfg()['reglas']
        alertas = {}

        def alerta(clave, titulo, mensaje, nivel='error', ruta='/'):
            alertas[clave] = {'titulo': titulo, 'mensaje': mensaje, 'nivel': nivel, 'ruta': ruta}

        instantanea = self.colector.snapshot()          # sin las instancias ocultas
        if not instantanea['meta'].get('ultimo_refresco'):
            return None                                  # todavía no hay datos
        for inst in instantanea['instancias']:
            r = inst.get('resumen') or {}
            nombre = '%s (%s)' % (inst.get('cliente'), inst.get('tipo'))
            ruta = '/?q=%s' % inst.get('cliente')
            servicio = inst.get('servicio_estado') or {}
            # Sólo se avisa de lo que debería estar arriba: un servicio
            # deshabilitado a propósito (cliente dado de baja) no es una caída.
            habilitado = (servicio.get('habilitado') in ('enabled', 'enabled-runtime', 'static')
                          or r.get('socket_existe'))
            if reglas.get('servicio_caido') and servicio.get('existe') and habilitado \
                    and not r.get('atiende'):
                alerta('servicio:%s' % inst['id'], 'Servicio caído: %s' % nombre,
                       'El servicio %s está %s.' % (inst.get('servicio'), servicio.get('estado')),
                       ruta=ruta)
            if reglas.get('url_caida') and r.get('atiende') and r.get('apache_habilitado') \
                    and r.get('url_responde') is False:
                alerta('url:%s' % inst['id'], 'No responde: %s' % (inst.get('url') or nombre),
                       '%s: %s' % (nombre, (inst.get('url_estado') or {}).get('error')
                                   or 'sin respuesta'), ruta=ruta)
            db = inst.get('db') or {}
            if reglas.get('base_caida') and not db.get('desactivado') and db.get('ok') is False \
                    and habilitado:
                alerta('bd:%s' % inst['id'], 'Base de datos inaccesible: %s' % nombre,
                       '%s: %s' % (db.get('dbname') or '-', db.get('error') or 'sin conexión'),
                       ruta=ruta)
            dias = r.get('ssl_dias')
            limite = int(reglas.get('ssl_dias') or 0)
            if limite and dias is not None and dias <= limite and r.get('apache_habilitado'):
                alerta('ssl:%s' % inst['id'],
                       ('Certificado VENCIDO: %s' if dias < 0 else 'Certificado por vencer: %s')
                       % (inst.get('dominio') or nombre),
                       '%s %s (vence %s).' % (nombre, 'venció hace %s días' % -dias if dias < 0
                                              else 'vence en %s días' % dias, r.get('ssl_hasta')),
                       nivel='error' if dias < 3 else 'aviso', ruta='/certificados')

        # Certificados de Let's Encrypt que no están ligados a una instancia
        # alertada (p. ej. el del propio panel o uno cuyo vhost no se detectó).
        limite = int(reglas.get('ssl_dias') or 0)
        if limite and os.path.isdir(certificados.RUTA_LIVE):
            ya_alertados = {(i.get('dominio') or '').lower() for i in instantanea['instancias']
                            if 'ssl:%s' % i['id'] in alertas}
            ocultos = {(i.get('dominio') or '').lower()
                       for i in self.colector.snapshot(incluir_ocultas=True)['instancias']
                       if i.get('oculta')}
            for nombre, vence in self._vencimientos():
                segundos = (vence - datetime.datetime.utcnow()).total_seconds()
                # Días completos: «vence en 3» y «venció hace 4» (sin redondear hacia afuera).
                dias = int(segundos // 86400) if segundos >= 0 else -int(-segundos // 86400) - 1
                dominio = nombre.lower()
                if dias > limite or dominio in ya_alertados or dominio in ocultos:
                    continue
                if certificados.estado_renovacion(nombre, self.config).get('renovacion') == 'pausada':
                    continue     # se dejó vencer a propósito
                alerta('cert:%s' % nombre,
                       ('Certificado VENCIDO: %s' if dias < 0 else 'Certificado por vencer: %s') % nombre,
                       ('Venció el %s (%s).' % (vence.strftime('%Y-%m-%d'),
                                                'hoy' if dias == -1 else 'hace %s días' % (-dias - 1))
                        if dias < 0
                        else 'Vence el %s (en %s días).' % (vence.strftime('%Y-%m-%d'), dias)),
                       nivel='error' if dias < 3 else 'aviso', ruta='/certificados')

        if reglas.get('servidor_web_caido'):
            for demonio, datos in (instantanea.get('servidores_web') or {}).items():
                if not datos.get('activo'):
                    alerta('web:%s' % demonio, '%s está caído' % demonio,
                           '%s está %s: ningún sitio de ese servidor responde.'
                           % (demonio, datos.get('estado')), ruta='/certificados')

        limite = int(reglas.get('disco_pct') or 0)
        if limite:
            for disco in consumo.discos():
                if disco['porcentaje'] >= limite:
                    alerta('disco:%s' % disco['montaje'], 'Disco %s al %s %%'
                           % (disco['montaje'], disco['porcentaje']),
                           'Quedan %s libres de %s.' % (disco['libre'], disco['total']),
                           nivel='error' if disco['porcentaje'] >= 95 else 'aviso', ruta='/consumo')

        limite = int(reglas.get('ram_pct') or 0)
        recursos = instantanea.get('recursos') or {}
        if limite and (recursos.get('ram_pct') or 0) >= limite:
            alerta('ram', 'RAM al %s %%' % recursos['ram_pct'],
                   'Usada %s de %s.' % (recursos.get('ram_usada_legible'),
                                        recursos.get('ram_total_legible')),
                   nivel='aviso', ruta='/consumo')

        if reglas.get('cobro_vencido') and self.cobros is not None:
            for fila in self.cobros.resumen(instantanea['instancias'])['instancias']:
                cobro = fila['cobro']
                if cobro.get('estado') != 'vencido':
                    continue
                alerta('cobro:%s' % fila['id'],
                       'Pago vencido: %s' % fila['cliente'],
                       '%s periodo(s) sin pagar · $%.2f · el más antiguo hace %s días.'
                       % (cobro['vencidos'], cobro['total_vencido'], cobro['dias_vencido']),
                       nivel='aviso', ruta='/cobros?q=%s' % fila['cliente'])

        if reglas.get('backup_atrasado') and (self.config.get('backups') or {}).get('enabled', True):
            datos = mod_backups.listar(self.config, instantanea['instancias'])
            for fila in datos['instancias']:
                if not fila.get('id'):
                    continue
                if fila['dias'] is None or fila['dias'] > datos['alerta_dias']:
                    alerta('backup:%s' % fila['id'], 'Backup atrasado: %s' % fila['cliente'],
                           ('Sin ningún backup.' if fila['dias'] is None
                            else 'El último tiene %s días.' % fila['dias']),
                           nivel='aviso', ruta='/backups')

        if reglas.get('renovacion_automatica') and os.path.isdir(certificados.RUTA_LIVE) \
                and os.listdir(certificados.RUTA_LIVE):
            if not certificados.renovacion_automatica().get('activa'):
                alerta('certbot:automatica', 'Sin renovación automática de certificados',
                       'Ni certbot.timer ni el cron de certbot están activos.',
                       nivel='aviso', ruta='/certificados')
        return alertas

    def _vencimientos(self):
        """(nombre, vencimiento) de cada certificado en /etc/letsencrypt/live.

        openssl sólo se ejecuta cuando el archivo cambió (se renovó).
        """
        from . import certificados
        salida = []
        for nombre in sorted(os.listdir(certificados.RUTA_LIVE)):
            ruta = os.path.join(certificados.RUTA_LIVE, nombre, 'fullchain.pem')
            try:
                marca = os.stat(ruta).st_mtime
            except OSError:
                continue
            previo = self._vence_cert.get(ruta)
            if not previo or previo[0] != marca:
                previo = (marca, certificados._fechas_openssl(ruta).get('vence'))
                self._vence_cert[ruta] = previo
            if previo[1]:
                salida.append((nombre, previo[1]))
        return salida

    def invalidar_alertas(self):
        """Tras un cambio hecho desde el panel (p. ej. un pago), recalcular al momento."""
        self._cache_alertas = None

    def alertas_actuales(self, ttl=30):
        """Lo que está mal AHORA, sin esperar confirmaciones: para el modal del panel.

        A diferencia de las notificaciones (que esperan a que el problema se
        repita para no molestar por un reinicio), aquí se muestra al momento.
        """
        ahora = time.time()
        cache = self._cache_alertas
        if cache and ahora - cache[0] < ttl:
            return cache[1]
        try:
            actuales = self.evaluar()
        except Exception as ex:  # pragma: no cover - el panel no debe caerse por esto
            actuales = {'panel:error': {'titulo': 'No se pudieron revisar las alertas',
                                        'mensaje': str(ex), 'nivel': 'aviso', 'ruta': '/'}}
        if actuales is None:
            return None          # el colector todavía no terminó el primer refresco
        with self._lock:
            self._cargar()
            estado = dict(self._estado or {})
        momento = ahora_iso()
        for clave in list(self._vistas):
            if clave not in actuales:
                del self._vistas[clave]
        lista = []
        for clave, datos in actuales.items():
            desde = (estado.get(clave) or {}).get('desde') or self._vistas.setdefault(clave, momento)
            lista.append(dict(datos, clave=clave, desde=desde))
        orden = {'error': 0, 'aviso': 1}
        lista.sort(key=lambda a: (orden.get(a.get('nivel'), 2), a.get('titulo') or ''))
        self._cache_alertas = (ahora, lista)
        return lista

    def revisar(self):
        """Una vuelta: compara las alertas con la anterior y notifica los cambios."""
        cfg = self.cfg()
        actuales = self.evaluar()
        if actuales is None:
            return
        self._cache_alertas = None
        self.ultima_revision = ahora_iso()
        ahora = time.time()
        confirmaciones = int(cfg.get('confirmaciones') or 1)
        repetir = float(cfg.get('repetir_horas') or 0) * 3600
        nuevas, resueltas, recordar = [], [], []

        with self._lock:
            self._cargar()
            estado = self._estado
            for clave, datos in actuales.items():
                previo = estado.get(clave) or {'veces': 0, 'desde': ahora_iso(),
                                               'notificada': False, 'ultimo_aviso': 0}
                previo.update(datos)
                previo['veces'] = previo.get('veces', 0) + 1
                if not previo['notificada'] and previo['veces'] >= confirmaciones:
                    previo['notificada'] = True
                    previo['ultimo_aviso'] = ahora
                    nuevas.append(dict(previo, clave=clave))
                elif previo['notificada'] and repetir and ahora - previo.get('ultimo_aviso', 0) >= repetir:
                    previo['ultimo_aviso'] = ahora
                    recordar.append(dict(previo, clave=clave))
                estado[clave] = previo
            for clave in list(estado):
                if clave not in actuales:
                    if estado[clave].get('notificada'):
                        resueltas.append(dict(estado[clave], clave=clave))
                    del estado[clave]
            self._escribir('alertas_estado.json', estado)

        if nuevas:
            self._enviar_lote(nuevas, 'nueva')
        if recordar:
            self._enviar_lote(recordar, 'sigue')
        if resueltas and cfg.get('avisar_recuperacion', True):
            self._enviar_lote(resueltas, 'resuelta')

    def _enviar_lote(self, alertas, tipo):
        if tipo == 'resuelta':
            entradas = [{'titulo': 'Resuelto: %s' % a['titulo'],
                         'mensaje': 'Volvió a la normalidad (desde %s).' % a.get('desde'),
                         'nivel': 'ok', 'ruta': a.get('ruta') or '/'} for a in alertas]
        else:
            prefijo = 'Sigue: ' if tipo == 'sigue' else ''
            entradas = [{'titulo': prefijo + a['titulo'], 'mensaje': a['mensaje'],
                         'nivel': a.get('nivel') or 'error', 'ruta': a.get('ruta') or '/'}
                        for a in alertas]
        if len(entradas) == 1:
            e = entradas[0]
            titulo, mensaje, nivel, ruta = e['titulo'], e['mensaje'], e['nivel'], e['ruta']
        else:
            nivel = ('ok' if tipo == 'resuelta'
                     else 'error' if any(e['nivel'] == 'error' for e in entradas) else 'aviso')
            titulo = {'nueva': '%s alertas nuevas', 'sigue': '%s alertas siguen activas',
                      'resuelta': '%s alertas resueltas'}[tipo] % len(entradas)
            mensaje = '\n'.join('• %s — %s' % (e['titulo'], e['mensaje']) for e in entradas[:25])
            if len(entradas) > 25:
                mensaje += '\n… y %s más' % (len(entradas) - 25)
            ruta = '/notificaciones'
        self.publicar(titulo, mensaje, nivel, ruta, entradas=entradas)

    # ---------------------------------------------------------------- tareas
    def tarea_terminada(self, tarea):
        if not self.cfg()['reglas'].get('tareas', True):
            return
        ok = tarea.estado == 'ok'
        problemas = (tarea.datos or {}).get('problemas')
        detalle = tarea.error or ('; '.join(problemas) if problemas else '')
        self.publicar('%s: %s' % ('Tarea terminada' if ok else 'Tarea con error', tarea.titulo),
                      detalle or ('Terminó sin problemas (lanzada por %s).'
                                  % (tarea.creado_por or '-')),
                      'ok' if ok and not problemas else ('aviso' if ok else 'error'),
                      '/notificaciones?tarea=%s' % tarea.id)

    # ------------------------------------------------------------------ hilo
    def iniciar_en_segundo_plano(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(target=self._bucle, name='notificador', daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()

    def _bucle(self):
        # Se deja terminar el primer refresco del colector antes de revisar.
        self._parar.wait(60)
        while not self._parar.is_set():
            if self.cfg().get('enabled', True):
                try:
                    self.revisar()
                except Exception as ex:  # pragma: no cover - el hilo no debe morir
                    self.ultima_revision = 'error: %s' % ex
            self._parar.wait(max(60, int(self.cfg().get('intervalo') or 300)))
