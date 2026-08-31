# -*- coding: utf-8 -*-
"""Utilidades comunes del panel."""
from __future__ import annotations

import datetime
import subprocess


def ahora_iso() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=' ')


def bytes_legible(valor) -> str:
    """Convierte bytes a una cadena legible (KB, MB, GB...)."""
    if valor is None:
        return '-'
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return '-'
    if valor < 0:
        return '-'
    unidades = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    i = 0
    while valor >= 1024 and i < len(unidades) - 1:
        valor /= 1024.0
        i += 1
    if i == 0:
        return '%d %s' % (int(valor), unidades[i])
    return '%.2f %s' % (valor, unidades[i])


def duracion_legible(segundos) -> str:
    """Convierte segundos a '3d 4h 12m'."""
    if segundos is None:
        return '-'
    try:
        segundos = int(segundos)
    except (TypeError, ValueError):
        return '-'
    if segundos < 0:
        return '-'
    dias, resto = divmod(segundos, 86400)
    horas, resto = divmod(resto, 3600)
    minutos = resto // 60
    partes = []
    if dias:
        partes.append('%dd' % dias)
    if horas or dias:
        partes.append('%dh' % horas)
    partes.append('%dm' % minutos)
    return ' '.join(partes)


def fecha_iso(valor):
    """Normaliza date/datetime/time a texto ISO; deja pasar None y str."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        return valor.replace(microsecond=0).isoformat(sep=' ')
    if isinstance(valor, (datetime.date, datetime.time)):
        return valor.isoformat()
    return str(valor)


def dias_desde(valor):
    """Días transcurridos desde una fecha/hora (None si no aplica)."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        fecha = valor.date()
    elif isinstance(valor, datetime.date):
        fecha = valor
    else:
        return None
    return (datetime.date.today() - fecha).days


def ejecutar(comando, timeout=15):
    """Ejecuta un comando y devuelve (returncode, stdout, stderr).

    Nunca lanza excepción: los errores se devuelven en stderr.
    """
    try:
        proc = subprocess.run(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return (
            proc.returncode,
            proc.stdout.decode('utf-8', 'replace').strip(),
            proc.stderr.decode('utf-8', 'replace').strip(),
        )
    except subprocess.TimeoutExpired:
        return (124, '', 'timeout de %ss ejecutando: %s' % (timeout, ' '.join(comando)))
    except FileNotFoundError:
        return (127, '', 'comando no encontrado: %s' % comando[0])
    except Exception as ex:  # pragma: no cover - defensivo
        return (1, '', str(ex))
