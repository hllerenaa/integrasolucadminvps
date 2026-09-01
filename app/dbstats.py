# -*- coding: utf-8 -*-
"""Consultas a la base de datos PostgreSQL de cada instancia.

Los dos sistemas (pryinventario y pryrestaurante) comparten buena parte del
esquema (Django 2.2), pero las tablas de ventas cambian. Por eso todo se
resuelve por DETECCIÓN: se consulta information_schema y se usa la primera
tabla/columna que realmente exista en esa base.
"""
from __future__ import annotations

import datetime
import time

from .utils import bytes_legible, dias_desde, fecha_iso

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_ERROR = None
except Exception as ex:  # pragma: no cover - entorno sin driver
    psycopg2 = None
    PSYCOPG2_ERROR = str(ex)

# Tablas comunes a ambos sistemas.
TABLA_AUDITORIA = 'seguridad_audiusuariotabla'
TABLA_CONFIGURACION = 'seguridad_configuracion'
TABLA_SESIONES = 'django_session'
TABLA_USUARIOS = 'auth_user'
TABLA_CONECTADOS = 'seguridad_usuarioconectado'

# Búsqueda de personas por cédula vía API externa: la columna cambia según el
# sistema (inventario / restaurante) y la versión, así que se detecta.
COLUMNAS_API_CEDULA = ('usar_api_persona', 'traer_api_cliente', 'usar_api_personas',
                       'usar_api_cliente', 'traer_api_persona')

# Campos de seguridad_configuracion que se pueden editar desde el panel.
CAMPOS_CONFIGURACION_EDITABLES = ('rucproveedor', 'ruc', 'nombre_empresa', 'telefono_empresa')

# Tablas de ventas por tipo de sistema, en orden de prioridad.
# (tabla, etiqueta) -> la columna de fecha se detecta entre COLUMNAS_FECHA.
VENTAS = {
    'inventario': [
        ('salida_salidaservicios', 'Ventas'),
        ('facturacion_facturareal', 'Facturas electrónicas'),
        ('servicios_factura', 'Entrega de equipos'),
        ('salida_salidaproducto', 'Salidas de producto'),
    ],
    'restaurante': [
        ('pedido_venta', 'Ventas'),
        ('facturacion_facturareal', 'Facturas electrónicas'),
        ('pedido_pedido', 'Pedidos'),
    ],
}

COLUMNAS_FECHA = ('fecha', 'fecha_registro', 'fechasalida')


def _tablas_de_interes(tipo):
    tablas = {TABLA_AUDITORIA, TABLA_CONFIGURACION, TABLA_SESIONES,
              TABLA_USUARIOS, TABLA_CONECTADOS}
    for tabla, _ in VENTAS.get(tipo, []):
        tablas.add(tabla)
    return sorted(tablas)


def _esquema(cur, tipo):
    """Devuelve {tabla: set(columnas)} de las tablas que existen en la base."""
    cur.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (_tablas_de_interes(tipo),),
    )
    esquema = {}
    for tabla, columna in cur.fetchall():
        esquema.setdefault(tabla, set()).add(columna)
    return esquema


def _columna_fecha(columnas):
    for columna in COLUMNAS_FECHA:
        if columna in columnas:
            return columna
    return None


def _filtro_status(columnas):
    return ' WHERE status IS TRUE' if 'status' in columnas else ''


def _estimado(cur, tabla):
    """Número aproximado de filas (rápido, sin escanear la tabla)."""
    try:
        cur.execute(
            """
            SELECT GREATEST(c.reltuples, 0)::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = %s
            """,
            (tabla,),
        )
        fila = cur.fetchone()
        estimado = int(fila[0]) if fila and fila[0] is not None else None
    except Exception:
        return None
    if estimado:
        return estimado
    # La tabla nunca fue analizada (reltuples <= 0): se cuenta de verdad,
    # protegido por el statement_timeout de la conexión.
    try:
        cur.execute('SELECT COUNT(*) FROM %s' % tabla)
        return int(cur.fetchone()[0])
    except Exception:
        return estimado


def columna_api_cedula(columnas):
    """Nombre real de la columna de búsqueda por API, si existe."""
    for columna in COLUMNAS_API_CEDULA:
        if columna in (columnas or set()):
            return columna
    return None


def _api_cedula(cur, esquema):
    """Si la instancia consulta la API de personas (cédula) y con qué columna."""
    columnas = esquema.get(TABLA_CONFIGURACION)
    if not columnas:
        return {'disponible': False, 'error': 'Sin tabla de configuración'}
    columna = columna_api_cedula(columnas)
    if not columna:
        return {'disponible': False, 'error': 'Esta versión no tiene la opción'}
    try:
        cur.execute('SELECT %s FROM %s ORDER BY id LIMIT 1' % (columna, TABLA_CONFIGURACION))
        fila = cur.fetchone()
    except Exception as ex:
        return {'disponible': False, 'columna': columna, 'error': str(ex).strip()}
    return {'disponible': True, 'columna': columna,
            'activa': bool(fila[0]) if fila else None}


def _empresa(cur, esquema):
    columnas = esquema.get(TABLA_CONFIGURACION)
    if not columnas:
        return {}
    campos = [c for c in ('nombre_empresa', 'ruc', 'rucproveedor', 'web',
                          'razonsocial', 'alias', 'telefono_empresa')
              if c in columnas]
    if not campos:
        return {}
    try:
        cur.execute('SELECT %s FROM %s ORDER BY id LIMIT 1'
                    % (', '.join(campos), TABLA_CONFIGURACION))
        fila = cur.fetchone()
        if not fila:
            return {}
        datos = {campo: fila[i] for i, campo in enumerate(campos)}
        # Qué campos existen en esta versión (para saber dónde falta migrar)
        datos['_campos'] = campos
        datos['_rucproveedor_disponible'] = 'rucproveedor' in columnas
        return datos
    except Exception as ex:
        return {'error': str(ex).strip()}


def _auditoria(cur, esquema):
    """Último registro de la tabla de auditoría (seguridad_audiusuariotabla)."""
    columnas = esquema.get(TABLA_AUDITORIA)
    if not columnas:
        return {'disponible': False, 'error': 'Tabla %s inexistente' % TABLA_AUDITORIA}

    datos = {'disponible': True, 'tabla': TABLA_AUDITORIA}
    tiene_usuario = 'usuario_id' in columnas and TABLA_USUARIOS in esquema
    campos = ['a.fecha', 'a.hora' if 'hora' in columnas else 'NULL::time',
              'a.tabla' if 'tabla' in columnas else "''",
              'a.accion' if 'accion' in columnas else "''",
              'a.registroname' if 'registroname' in columnas else "''",
              'u.username' if tiene_usuario else "''"]
    join = ('LEFT JOIN %s u ON u.id = a.usuario_id' % TABLA_USUARIOS) if tiene_usuario else ''
    orden = 'a.fecha DESC, a.hora DESC' if 'hora' in columnas else 'a.fecha DESC'
    try:
        cur.execute('SELECT %s FROM %s a %s ORDER BY %s LIMIT 1'
                    % (', '.join(campos), TABLA_AUDITORIA, join, orden))
        fila = cur.fetchone()
    except Exception as ex:
        datos['error'] = str(ex).strip()
        return datos

    if not fila:
        datos['fecha'] = None
        datos['total_aprox'] = 0
        return datos

    datos.update({
        'fecha': fecha_iso(fila[0]),
        'hora': fecha_iso(fila[1]),
        'tabla_afectada': fila[2],
        'accion': fila[3],
        'registro': fila[4],
        'usuario': fila[5],
        'dias': dias_desde(fila[0]),
        'total_aprox': _estimado(cur, TABLA_AUDITORIA),
    })
    return datos


def _sesiones(cur, esquema):
    """Último inicio de sesión y sesiones vigentes."""
    datos = {'disponible': False}

    if TABLA_USUARIOS in esquema:
        datos['disponible'] = True
        try:
            cur.execute(
                'SELECT username, last_login FROM %s '
                'WHERE last_login IS NOT NULL ORDER BY last_login DESC LIMIT 1'
                % TABLA_USUARIOS
            )
            fila = cur.fetchone()
            if fila:
                datos['usuario'] = fila[0]
                datos['ultimo_login'] = fecha_iso(fila[1])
                datos['dias'] = dias_desde(fila[1])
        except Exception as ex:
            datos['error'] = str(ex).strip()
        try:
            cur.execute('SELECT COUNT(*) FROM %s WHERE is_active IS TRUE' % TABLA_USUARIOS)
            datos['usuarios_activos'] = cur.fetchone()[0]
        except Exception:
            pass

    if TABLA_SESIONES in esquema:
        try:
            cur.execute('SELECT COUNT(*) FROM %s WHERE expire_date > NOW()' % TABLA_SESIONES)
            datos['sesiones_vigentes'] = cur.fetchone()[0]
        except Exception as ex:
            datos.setdefault('error', str(ex).strip())

    columnas_conectados = esquema.get(TABLA_CONECTADOS)
    if columnas_conectados and 'fecha_conexion' in columnas_conectados:
        try:
            cur.execute('SELECT MAX(fecha_conexion) FROM %s' % TABLA_CONECTADOS)
            fila = cur.fetchone()
            if fila and fila[0]:
                datos['ultima_conexion'] = fecha_iso(fila[0])
        except Exception:
            pass

    return datos


def _ventas(cur, esquema, tipo):
    """Primera y última venta en cada tabla de ventas detectada."""
    salida = []
    for tabla, etiqueta in VENTAS.get(tipo, []):
        columnas = esquema.get(tabla)
        if not columnas:
            continue
        columna = _columna_fecha(columnas)
        if not columna:
            continue
        registro = {'tabla': tabla, 'etiqueta': etiqueta, 'columna': columna}
        try:
            # Una sola pasada por la tabla: totales y desglose por año.
            consulta = ('SELECT EXTRACT(YEAR FROM {c})::int AS anio, COUNT(*), '
                        'MIN({c}), MAX({c}) FROM {t}{f} GROUP BY 1 ORDER BY 1'
                        ).format(c=columna, t=tabla, f=_filtro_status(columnas))
            cur.execute(consulta)
            filas = cur.fetchall()
            por_anio = [{'anio': f[0], 'total': int(f[1])} for f in filas if f[0] is not None]
            registro['por_anio'] = list(reversed(por_anio))   # el más reciente primero
            registro['total'] = sum(int(f[1]) for f in filas)
            minimos = [f[2] for f in filas if f[2] is not None]
            maximos = [f[3] for f in filas if f[3] is not None]
            registro['primera'] = fecha_iso(min(minimos)) if minimos else None
            registro['ultima'] = fecha_iso(max(maximos)) if maximos else None
            registro['dias_sin_ventas'] = dias_desde(max(maximos)) if maximos else None
            anio = datetime.date.today().year
            registro['anio_actual'] = next(
                (a['total'] for a in por_anio if a['anio'] == anio), 0)
            registro['anio_anterior'] = next(
                (a['total'] for a in por_anio if a['anio'] == anio - 1), 0)
        except Exception as ex:
            registro['error'] = str(ex).strip()
            registro['total'] = _estimado(cur, tabla)
        salida.append(registro)
    return salida


TABLA_FACTURAS = 'facturacion_facturareal'


def _facturacion(cur, esquema):
    """Facturas emitidas: total, del mes actual y último mes con facturación."""
    columnas = esquema.get(TABLA_FACTURAS)
    if not columnas:
        return {'disponible': False, 'error': 'Tabla %s inexistente' % TABLA_FACTURAS}
    columna = _columna_fecha(columnas)
    if not columna:
        return {'disponible': False, 'error': 'Sin columna de fecha en %s' % TABLA_FACTURAS}

    datos = {'disponible': True, 'tabla': TABLA_FACTURAS}
    try:
        consulta = ('SELECT EXTRACT(YEAR FROM {c})::int, COUNT(*) FROM {t}{f} '
                    'GROUP BY 1 ORDER BY 1 DESC'
                    ).format(c=columna, t=TABLA_FACTURAS, f=_filtro_status(columnas))
        cur.execute(consulta)
        datos['por_anio'] = [{'anio': f[0], 'total': int(f[1])}
                             for f in cur.fetchall() if f[0] is not None]
    except Exception:
        datos['por_anio'] = []
    try:
        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN {col} >= date_trunc('month', CURRENT_DATE) THEN 1 ELSE 0 END),
                   SUM(CASE WHEN {col} >= date_trunc('month', CURRENT_DATE) - interval '1 month'
                             AND {col} <  date_trunc('month', CURRENT_DATE) THEN 1 ELSE 0 END),
                   MIN({col}), MAX({col})
            FROM {tabla}{filtro}
        """.format(col=columna, tabla=TABLA_FACTURAS, filtro=_filtro_status(columnas)))
        fila = cur.fetchone()
    except Exception as ex:
        datos['error'] = str(ex).strip()
        return datos

    total, mes_actual, mes_anterior, primera, ultima = fila
    datos.update({
        'total': int(total or 0),
        'mes_actual': int(mes_actual or 0),
        'mes_anterior': int(mes_anterior or 0),
        'primera': fecha_iso(primera),
        'ultima': fecha_iso(ultima),
        'dias_sin_facturar': dias_desde(ultima),
    })

    if ultima is not None:
        hoy = datetime.date.today()
        datos['ultimo_mes'] = '%04d-%02d' % (ultima.year, ultima.month)
        datos['meses_sin_facturar'] = ((hoy.year - ultima.year) * 12) + (hoy.month - ultima.month)
    else:
        datos['ultimo_mes'] = None
        datos['meses_sin_facturar'] = None

    # Semáforo: facturó este mes / sólo el mes pasado / dejó de facturar.
    if datos['mes_actual']:
        datos['estado'] = 'facturando'
    elif datos['meses_sin_facturar'] == 1:
        datos['estado'] = 'sin-facturar-mes'
    elif datos['meses_sin_facturar'] is None:
        datos['estado'] = 'nunca'
    else:
        datos['estado'] = 'detenido'
    return datos


def _tablas_grandes(cur, limite=5):
    try:
        cur.execute(
            """
            SELECT c.relname, pg_total_relation_size(c.oid) AS tam
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY tam DESC
            LIMIT %s
            """,
            (limite,),
        )
        return [{'tabla': f[0], 'bytes': int(f[1]), 'tamano': bytes_legible(f[1])}
                for f in cur.fetchall()]
    except Exception:
        return []


def consultar(instancia, config):
    """Recopila todos los datos de la base de datos de una instancia."""
    inicio = time.time()
    datos = {
        'ok': False,
        'host': None,
        'dbname': None,
        'error': None,
        'tamano_bytes': None,
        'tamano': '-',
        'version': None,
        'latencia_ms': None,
        'auditoria': {},
        'sesiones': {},
        'ventas': [],
        'facturacion': {},
        'api_cedula': {},
        'empresa': {},
        'tablas_grandes': [],
    }

    if psycopg2 is None:
        datos['error'] = 'psycopg2 no está instalado (%s)' % PSYCOPG2_ERROR
        return datos

    conexion = instancia.base_datos()
    datos['host'] = conexion.get('host')
    datos['dbname'] = conexion.get('dbname')
    if not conexion.get('dbname') or not conexion.get('host'):
        datos['error'] = instancia.error_credenciales or 'credenciales.json sin datos de PostgreSQL'
        return datos

    conn = None
    try:
        conn = psycopg2.connect(
            host=conexion['host'],
            port=int(conexion.get('port') or 5432),
            dbname=conexion['dbname'],
            user=conexion.get('user'),
            password=conexion.get('password'),
            connect_timeout=int(config.get('db_connect_timeout') or 6),
            application_name='integrasolucadminvps',
        )
        conn.autocommit = True  # cada consulta es independiente: un error no aborta el resto
        cur = conn.cursor()
        cur.execute("SET statement_timeout = %s", (int(config.get('db_statement_timeout') or 15000),))
        datos['latencia_ms'] = int((time.time() - inicio) * 1000)
        datos['ok'] = True

        try:
            cur.execute('SELECT current_setting(%s), pg_database_size(current_database())',
                        ('server_version',))
            fila = cur.fetchone()
            datos['version'] = fila[0]
            datos['tamano_bytes'] = int(fila[1])
            datos['tamano'] = bytes_legible(fila[1])
        except Exception as ex:
            datos['error'] = str(ex).strip()

        esquema = _esquema(cur, instancia.tipo)
        datos['tablas_detectadas'] = sorted(esquema.keys())
        datos['empresa'] = _empresa(cur, esquema)
        datos['auditoria'] = _auditoria(cur, esquema)
        datos['sesiones'] = _sesiones(cur, esquema)
        datos['ventas'] = _ventas(cur, esquema, instancia.tipo)
        datos['facturacion'] = _facturacion(cur, esquema)
        datos['api_cedula'] = _api_cedula(cur, esquema)
        datos['tablas_grandes'] = _tablas_grandes(cur)
        cur.close()
    except Exception as ex:
        datos['ok'] = False
        datos['error'] = str(ex).strip().splitlines()[0] if str(ex).strip() else repr(ex)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    datos['duracion_ms'] = int((time.time() - inicio) * 1000)
    return datos


def listar_bases(instancia, config):
    """Todas las bases del servidor PostgreSQL con su tamaño."""
    if psycopg2 is None:
        return {'ok': False, 'error': 'psycopg2 no está instalado', 'bases': []}
    conexion = instancia.base_datos()
    conn = None
    try:
        conn = psycopg2.connect(
            host=conexion['host'], port=int(conexion.get('port') or 5432),
            dbname=conexion['dbname'], user=conexion.get('user'),
            password=conexion.get('password'),
            connect_timeout=int(config.get('db_connect_timeout') or 6),
            application_name='integrasolucadminvps')
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT d.datname,
                   pg_database_size(d.datname),
                   pg_get_userbyid(d.datdba),
                   (SELECT COUNT(*) FROM pg_stat_activity a WHERE a.datname = d.datname)
            FROM pg_database d
            WHERE NOT d.datistemplate AND d.datallowconn
            ORDER BY pg_database_size(d.datname) DESC
        """)
        bases = [{'nombre': f[0], 'bytes': int(f[1]), 'tamano': bytes_legible(f[1]),
                  'dueno': f[2], 'conexiones': int(f[3])} for f in cur.fetchall()]
        cur.close()
        return {'ok': True, 'host': conexion['host'], 'bases': bases}
    except Exception as ex:
        return {'ok': False, 'error': str(ex).strip().splitlines()[0], 'bases': []}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def cambiar_configuracion(instancia, config, campo, valor):
    """Actualiza un campo de seguridad_configuracion (lista blanca)."""
    if campo not in CAMPOS_CONFIGURACION_EDITABLES:
        return {'ok': False, 'error': 'Campo no editable desde el panel: %s' % campo}
    if psycopg2 is None:
        return {'ok': False, 'error': 'psycopg2 no está instalado'}
    conexion = instancia.base_datos()
    if not conexion.get('dbname'):
        return {'ok': False, 'error': 'Sin datos de PostgreSQL en credenciales.json'}

    conn = None
    try:
        conn = psycopg2.connect(
            host=conexion['host'], port=int(conexion.get('port') or 5432),
            dbname=conexion['dbname'], user=conexion.get('user'),
            password=conexion.get('password'),
            connect_timeout=int(config.get('db_connect_timeout') or 6),
            application_name='integrasolucadminvps')
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET statement_timeout = %s", (int(config.get('db_statement_timeout') or 15000),))
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            """,
            (TABLA_CONFIGURACION, campo))
        if not cur.fetchone():
            return {'ok': False,
                    'error': 'Esta base todavía no tiene la columna %s '
                             '(falta aplicar la migración)' % campo}

        cur.execute('SELECT COUNT(*) FROM %s' % TABLA_CONFIGURACION)
        if not cur.fetchone()[0]:
            return {'ok': False, 'error': 'La tabla de configuración está vacía'}

        cur.execute('UPDATE %s SET %s = %%s' % (TABLA_CONFIGURACION, campo), (valor or None,))
        filas = cur.rowcount
        cur.close()
        return {'ok': True, 'campo': campo, 'valor': valor, 'filas': filas}
    except Exception as ex:
        return {'ok': False, 'error': str(ex).strip().splitlines()[0]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def cambiar_api_cedula(instancia, config, activar):
    """Activa o desactiva la búsqueda de personas por cédula en esa instancia."""
    if psycopg2 is None:
        return {'ok': False, 'error': 'psycopg2 no está instalado'}
    conexion = instancia.base_datos()
    if not conexion.get('dbname'):
        return {'ok': False, 'error': 'Sin datos de PostgreSQL en credenciales.json'}
    conn = None
    try:
        conn = psycopg2.connect(
            host=conexion['host'], port=int(conexion.get('port') or 5432),
            dbname=conexion['dbname'], user=conexion.get('user'),
            password=conexion.get('password'),
            connect_timeout=int(config.get('db_connect_timeout') or 6),
            application_name='integrasolucadminvps')
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET statement_timeout = %s", (int(config.get('db_statement_timeout') or 15000),))
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = ANY(%s)
            """,
            (TABLA_CONFIGURACION, list(COLUMNAS_API_CEDULA)))
        fila = cur.fetchone()
        if not fila:
            return {'ok': False, 'error': 'La base no tiene la columna de búsqueda por API'}
        columna = fila[0]
        cur.execute('UPDATE %s SET %s = %%s' % (TABLA_CONFIGURACION, columna), (bool(activar),))
        afectadas = cur.rowcount
        cur.close()
        return {'ok': True, 'columna': columna, 'activa': bool(activar), 'filas': afectadas}
    except Exception as ex:
        return {'ok': False, 'error': str(ex).strip().splitlines()[0]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def venta_principal(datos_db):
    """Devuelve la tabla de ventas principal detectada (la de mayor prioridad)."""
    for registro in datos_db.get('ventas') or []:
        if registro.get('primera') or registro.get('ultima'):
            return registro
    ventas = datos_db.get('ventas') or []
    return ventas[0] if ventas else {}
