# -*- coding: utf-8 -*-
"""Cobros a los clientes: plan mensual o anual, fecha de corte y pagos por periodo.

Cada instancia puede tener un plan:

- **mensual**: vence todos los meses el mismo día que la primera fecha de
  corte (si el mes es más corto, el último día del mes). Periodo «2026-09».
- **anual**: vence cada año en la misma fecha. Periodo «2026».

Los periodos se generan desde la primera fecha de corte hasta el próximo que
viene. Cada uno se marca como pagado o no; lo no pagado con la fecha ya
pasada (más los días de gracia) está vencido y suma a la deuda.

Todo se guarda en var/cobros.json (no depende de las bases de los clientes).
"""
from __future__ import annotations

import calendar
import datetime
import json
import os
import threading

from .utils import ahora_iso

PLANES = ('mensual', 'anual')
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre')
PERIODOS_ADELANTE = 2          # periodos futuros que se muestran para pagar por adelantado

_LOCK = threading.Lock()


# ------------------------------------------------------------------ fechas
def _fecha(texto):
    if isinstance(texto, datetime.date):
        return texto
    try:
        return datetime.datetime.strptime((texto or '').strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _con_dia(anio, mes, dia):
    """La fecha con ese día, o el último día del mes si no existe (31 → 30/28)."""
    return datetime.date(anio, mes, min(dia, calendar.monthrange(anio, mes)[1]))


def _vencimiento(plan, primera, n):
    """Fecha de corte del periodo n (0 = el primero)."""
    if plan == 'anual':
        return _con_dia(primera.year + n, primera.month, primera.day)
    anio, mes = divmod(primera.month - 1 + n, 12)
    return _con_dia(primera.year + anio, mes + 1, primera.day)


def _clave(plan, fecha):
    return str(fecha.year) if plan == 'anual' else '%04d-%02d' % (fecha.year, fecha.month)


def _nombre_periodo(plan, fecha):
    if plan == 'anual':
        return 'Año %s' % fecha.year
    return '%s %s' % (MESES[fecha.month - 1].capitalize(), fecha.year)


def _dinero(valor):
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ cálculo
def calcular(datos, hoy=None):
    """Estado de cobro de una instancia a partir de su configuración y pagos."""
    hoy = hoy or datetime.date.today()
    datos = datos or {}
    plan = datos.get('plan')
    primera = _fecha(datos.get('primera_fecha'))
    if plan not in PLANES or not primera:
        return {'plan': None, 'estado': 'sin-plan', 'notas': datos.get('notas') or ''}

    monto = _dinero(datos.get('monto'))
    gracia = max(0, int(datos.get('dias_gracia') or 0))
    aviso = max(0, int(datos.get('dias_aviso') if datos.get('dias_aviso') is not None else 5))
    pagos = datos.get('pagos') or {}

    periodos = []
    futuros = 0
    n = 0
    while n < 1200:                 # 100 años de meses: tope de seguridad
        vence = _vencimiento(plan, primera, n)
        if vence > hoy:
            futuros += 1
            if futuros > PERIODOS_ADELANTE:
                break
        clave = _clave(plan, vence)
        pago = pagos.get(clave) or {}
        pagado = bool(pago.get('pagado'))
        dias = (vence - hoy).days          # >0 falta, <0 pasó
        if pagado:
            situacion = 'pagado'
        elif dias < -gracia:
            situacion = 'vencido'
        elif dias < 0:
            situacion = 'en-gracia'
        elif dias <= aviso:
            situacion = 'por-vencer'
        else:
            situacion = 'pendiente'
        periodos.append({
            'periodo': clave, 'nombre': _nombre_periodo(plan, vence),
            'vence': vence.isoformat(), 'dias': dias, 'estado': situacion,
            'pagado': pagado, 'monto': monto,
            'pago': ({'fecha': pago.get('fecha'), 'monto': _dinero(pago.get('monto')),
                      'nota': pago.get('nota') or '', 'usuario': pago.get('usuario'),
                      'registrado': pago.get('registrado')} if pagado else None),
        })
        n += 1

    vencidos = [p for p in periodos if p['estado'] == 'vencido']
    en_gracia = [p for p in periodos if p['estado'] == 'en-gracia']
    pendientes = [p for p in periodos if not p['pagado']]
    proximo = pendientes[0] if pendientes else None
    pagados = [p for p in periodos if p['pagado']]
    ultimo_pago = max((p['pago'] for p in pagados if p['pago'] and p['pago'].get('fecha')),
                      key=lambda x: x['fecha'], default=None)
    # Último periodo pagado sin huecos desde el inicio («pagado hasta agosto»).
    pagado_hasta = None
    for p in periodos:
        if not p['pagado']:
            break
        pagado_hasta = p['nombre']

    if vencidos:
        estado = 'vencido'
    elif en_gracia:
        estado = 'en-gracia'
    elif proximo and proximo['estado'] == 'por-vencer':
        estado = 'por-vencer'
    else:
        estado = 'al-dia'

    return {
        'plan': plan,
        'monto': monto,
        'primera_fecha': primera.isoformat(),
        'corte': ('día %s de cada mes' % primera.day if plan == 'mensual'
                  else '%s de %s de cada año' % (primera.day, MESES[primera.month - 1])),
        'dias_gracia': gracia,
        'dias_aviso': aviso,
        'notas': datos.get('notas') or '',
        'estado': estado,
        'periodos': periodos,
        'vencidos': len(vencidos),
        'total_vencido': round(sum(p['monto'] for p in vencidos), 2),
        # Días del vencido más antiguo (lo que más le urge cobrar).
        'dias_vencido': (-vencidos[0]['dias'] if vencidos
                         else (-en_gracia[0]['dias'] if en_gracia else 0)),
        'proximo': ({'periodo': proximo['periodo'], 'nombre': proximo['nombre'],
                     'vence': proximo['vence'], 'dias': proximo['dias'],
                     'estado': proximo['estado']} if proximo else None),
        'pagado_hasta': pagado_hasta,
        'pagados': len(pagados),
        'total_pagado': round(sum((p['pago'] or {}).get('monto') or 0 for p in pagados), 2),
        'ultimo_pago': ultimo_pago,
    }


def compacto(estado):
    """Lo mínimo para la tabla de instancias (sin la lista de periodos)."""
    return {k: v for k, v in (estado or {}).items() if k != 'periodos'}


# --------------------------------------------------------------- almacenamiento
class Cobros(object):
    def __init__(self, config):
        self.config = config

    def _ruta(self):
        return os.path.join(self.config.var_dir, 'cobros.json')

    def _leer(self):
        try:
            with open(self._ruta(), 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _escribir(self, datos):
        ruta = self._ruta()
        with open(ruta + '.tmp', 'w', encoding='utf-8') as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=1)
        os.replace(ruta + '.tmp', ruta)
        try:
            os.chmod(ruta, 0o600)
        except OSError:
            pass

    def todos(self):
        with _LOCK:
            return self._leer()

    def estado(self, ident, hoy=None):
        return calcular(self.todos().get(ident), hoy)

    def configurar(self, ident, cambios, usuario=None):
        """Plan, monto, primera fecha de corte, días de gracia y de aviso, notas."""
        plan = cambios.get('plan') or None
        if plan not in PLANES and plan not in (None, 'ninguno'):
            raise ValueError('Plan desconocido: %s' % plan)
        primera = _fecha(cambios.get('primera_fecha'))
        if plan in PLANES and not primera:
            raise ValueError('Falta la primera fecha de corte')
        monto = _dinero(cambios.get('monto'))
        if plan in PLANES and monto <= 0:
            raise ValueError('El monto debe ser mayor que cero')
        with _LOCK:
            datos = self._leer()
            actual = datos.get(ident) or {}
            actual.update({
                'plan': plan if plan in PLANES else None,
                'monto': monto,
                'primera_fecha': primera.isoformat() if primera else None,
                # Lo que no llega se conserva (aviso: 5 días por defecto).
                'dias_gracia': max(0, int(cambios['dias_gracia'] if cambios.get('dias_gracia') is not None
                                          else actual.get('dias_gracia') or 0)),
                'dias_aviso': max(0, int(cambios['dias_aviso'] if cambios.get('dias_aviso') is not None
                                         else (actual.get('dias_aviso') if actual.get('dias_aviso') is not None
                                               else 5))),
                'notas': (cambios['notas'] if 'notas' in cambios else actual.get('notas') or '').strip()[:500],
                'actualizado': ahora_iso(), 'actualizado_por': usuario,
            })
            actual.setdefault('pagos', {})
            datos[ident] = actual
            self._escribir(datos)
        return calcular(actual)

    def registrar(self, ident, periodo, pagado, monto=None, fecha=None, nota='', usuario=None):
        """Marca un periodo como pagado (o lo desmarca)."""
        with _LOCK:
            datos = self._leer()
            actual = datos.get(ident)
            if not actual or actual.get('plan') not in PLANES:
                raise ValueError('Primero configura el plan de cobro de esta instancia')
            estado = calcular(actual)
            validos = {p['periodo'] for p in estado['periodos']}
            if periodo not in validos:
                raise ValueError('Periodo fuera del plan: %s' % periodo)
            pagos = actual.setdefault('pagos', {})
            if pagado:
                pagos[periodo] = {
                    'pagado': True,
                    'monto': _dinero(monto) if monto not in (None, '') else _dinero(actual.get('monto')),
                    'fecha': (_fecha(fecha) or datetime.date.today()).isoformat(),
                    'nota': (nota or '').strip()[:300],
                    'usuario': usuario, 'registrado': ahora_iso(),
                }
            else:
                pagos.pop(periodo, None)
            datos[ident] = actual
            self._escribir(datos)
        return calcular(actual)

    def resumen(self, instancias, hoy=None):
        """Estado de cobro de cada instancia y los totales para las tarjetas."""
        datos = self.todos()
        filas = []
        totales = {'con_plan': 0, 'al_dia': 0, 'por_vencer': 0, 'en_gracia': 0, 'vencidos': 0,
                   'sin_plan': 0, 'total_vencido': 0.0, 'mensual_esperado': 0.0,
                   'cobrar_30_dias': 0.0}
        hoy = hoy or datetime.date.today()
        for inst in instancias or []:
            estado = calcular(datos.get(inst.get('id')), hoy)
            filas.append({'id': inst.get('id'), 'cliente': inst.get('cliente'),
                          'tipo': inst.get('tipo'), 'oculta': bool(inst.get('oculta')),
                          'dominio': inst.get('dominio'),
                          'empresa': (inst.get('resumen') or {}).get('empresa') or '',
                          'servicio_activo': bool((inst.get('resumen') or {}).get('atiende')),
                          'cobro': compacto(estado)})
            clave = {'sin-plan': 'sin_plan', 'al-dia': 'al_dia', 'por-vencer': 'por_vencer',
                     'en-gracia': 'en_gracia', 'vencido': 'vencidos'}[estado['estado']]
            totales[clave] += 1
            if estado['plan']:
                totales['con_plan'] += 1
                totales['total_vencido'] += estado['total_vencido']
                totales['mensual_esperado'] += (estado['monto'] if estado['plan'] == 'mensual'
                                                else estado['monto'] / 12.0)
                for p in estado['periodos']:
                    if not p['pagado'] and 0 <= p['dias'] <= 30:
                        totales['cobrar_30_dias'] += p['monto']
        for clave in ('total_vencido', 'mensual_esperado', 'cobrar_30_dias'):
            totales[clave] = round(totales[clave], 2)
        return {'instancias': filas, 'totales': totales, 'hoy': hoy.isoformat()}
