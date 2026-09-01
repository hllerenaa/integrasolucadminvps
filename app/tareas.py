# -*- coding: utf-8 -*-
"""Tareas largas ejecutadas en segundo plano con log en vivo.

Las usa el asistente de creación de instancias: el navegador dispara la tarea,
recibe un id y va leyendo el log conforme avanza.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid

from .utils import ahora_iso


class Tarea(object):
    """Una ejecución con pasos, log y estado."""

    def __init__(self, tipo, titulo, config, datos=None):
        self.id = '%s-%s' % (time.strftime('%Y%m%d_%H%M%S'), uuid.uuid4().hex[:6])
        self.tipo = tipo
        self.titulo = titulo
        self.config = config
        self.datos = datos or {}
        self.estado = 'pendiente'          # pendiente | corriendo | ok | error | cancelada
        self.pasos = []                    # [{nombre, estado, detalle}]
        self.lineas = []
        self.creado = ahora_iso()
        self.fin = None
        self.error = None
        self.creado_por = None
        self.deshacer = []                 # acciones para revertir si algo falla
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ log
    def log(self, texto, nivel='info'):
        with self._lock:
            self.lineas.append({'t': time.strftime('%H:%M:%S'), 'nivel': nivel, 'texto': str(texto)})
        self._persistir()

    def paso(self, nombre):
        """Marca el inicio de un paso y devuelve su índice."""
        with self._lock:
            self.pasos.append({'nombre': nombre, 'estado': 'corriendo', 'detalle': None})
            indice = len(self.pasos) - 1
        self.log('▶ %s' % nombre, 'paso')
        return indice

    def paso_ok(self, indice, detalle=None):
        with self._lock:
            if 0 <= indice < len(self.pasos):
                self.pasos[indice].update({'estado': 'ok', 'detalle': detalle})
        if detalle:
            self.log('  %s' % detalle, 'ok')

    def paso_error(self, indice, detalle=None):
        with self._lock:
            if 0 <= indice < len(self.pasos):
                self.pasos[indice].update({'estado': 'error', 'detalle': detalle})
        if detalle:
            self.log('  %s' % detalle, 'error')

    def registrar_deshacer(self, tipo, valor):
        """Anota algo que se creó, para poder revertirlo si la tarea falla."""
        self.deshacer.append({'tipo': tipo, 'valor': valor})

    # -------------------------------------------------------------- comandos
    def ejecutar(self, comando, cwd=None, timeout=900, entorno=None, critico=True,
                 simular=False, ocultar=None):
        """Ejecuta un comando volcando su salida al log."""
        visible = ' '.join(comando) if isinstance(comando, (list, tuple)) else comando
        for secreto in (ocultar or []):
            # Sólo se enmascaran valores con longitud suficiente: ocultar una
            # cadena de 1-3 caracteres destrozaría el resto del comando.
            if secreto and len(str(secreto)) >= 5:
                visible = visible.replace(str(secreto), '••••')
        if simular:
            self.log('$ %s   [simulación, no se ejecuta]' % visible, 'cmd')
            return 0, ''
        self.log('$ %s' % visible, 'cmd')
        try:
            proc = subprocess.run(
                comando, cwd=cwd, timeout=timeout, env=entorno,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                shell=isinstance(comando, str),
            )
            salida = proc.stdout.decode('utf-8', 'replace').strip()
        except subprocess.TimeoutExpired:
            salida = 'timeout de %ss' % timeout
            proc = None
        except Exception as ex:
            salida = str(ex)
            proc = None

        codigo = proc.returncode if proc is not None else 124
        for linea in (salida or '').splitlines()[-40:]:
            self.log('  | %s' % linea, 'salida' if codigo == 0 else 'error')
        if codigo != 0 and critico:
            raise TareaError('El comando falló (código %s): %s' % (codigo, visible), salida)
        return codigo, salida

    # ------------------------------------------------------------ persistencia
    def _ruta(self):
        carpeta = os.path.join(self.config.var_dir, 'tareas')
        os.makedirs(carpeta, exist_ok=True)
        return os.path.join(carpeta, '%s.json' % self.id)

    def _persistir(self):
        try:
            with open(self._ruta(), 'w', encoding='utf-8') as fh:
                json.dump(self.as_dict(), fh, ensure_ascii=False)
        except OSError:
            pass

    def as_dict(self, desde=0):
        with self._lock:
            lineas = self.lineas[desde:]
            return {
                'id': self.id, 'tipo': self.tipo, 'titulo': self.titulo,
                'estado': self.estado, 'pasos': list(self.pasos),
                'creado': self.creado, 'fin': self.fin, 'error': self.error,
                'creado_por': self.creado_por, 'datos': self.datos,
                'lineas': lineas, 'total_lineas': len(self.lineas),
                'deshacer': list(self.deshacer),
            }


class TareaError(Exception):
    def __init__(self, mensaje, detalle=None):
        super(TareaError, self).__init__(mensaje)
        self.detalle = detalle


class GestorTareas(object):
    """Guarda las tareas de la sesión y las ejecuta en hilos aparte."""

    def __init__(self, config, maximo=40):
        self.config = config
        self.maximo = maximo
        self._tareas = {}
        self._orden = []
        self._lock = threading.Lock()

    def crear(self, tipo, titulo, datos=None, usuario=None):
        tarea = Tarea(tipo, titulo, self.config, datos)
        tarea.creado_por = usuario
        with self._lock:
            self._tareas[tarea.id] = tarea
            self._orden.append(tarea.id)
            sobran = self._orden[:-self.maximo] if len(self._orden) > self.maximo else []
            for viejo in sobran:
                self._orden.remove(viejo)
                self._tareas.pop(viejo, None)
        return tarea

    def obtener(self, ident):
        return self._tareas.get(ident)

    def listar(self):
        with self._lock:
            return [self._tareas[i].as_dict(desde=10 ** 9) for i in reversed(self._orden)
                    if i in self._tareas]

    def lanzar(self, tarea, funcion):
        """Corre la función en segundo plano pasándole la tarea."""
        def envoltura():
            tarea.estado = 'corriendo'
            try:
                funcion(tarea)
                if tarea.estado == 'corriendo':
                    tarea.estado = 'ok'
                tarea.log('✔ Proceso terminado correctamente', 'ok')
            except TareaError as ex:
                tarea.estado = 'error'
                tarea.error = str(ex)
                tarea.log('✖ %s' % ex, 'error')
            except Exception as ex:  # pragma: no cover - defensivo
                tarea.estado = 'error'
                tarea.error = str(ex)
                tarea.log('✖ Error inesperado: %s' % ex, 'error')
            finally:
                tarea.fin = ahora_iso()
                tarea._persistir()

        hilo = threading.Thread(target=envoltura, name='tarea-%s' % tarea.id, daemon=True)
        hilo.start()
        return tarea
