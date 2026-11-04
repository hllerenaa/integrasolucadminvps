# -*- coding: utf-8 -*-
"""Protecciones contra borrar lo ajeno: certificados, sockets y bases (H3 y H4).

Ejecutar con:  python -m unittest discover -s tests
Las pruebas de rutas necesitan Flask; sin él se omiten.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from app import aprovisionar as ap
from app import certificados as cert

from test_aprovisionar import Base

try:
    import flask  # noqa: F401
    HAY_FLASK = True
except ImportError:
    HAY_FLASK = False


def vhost(sitio, servername, contenido, certificado=None):
    return {'servidor': 'apache', 'sitio': sitio, 'nombre': '%s.conf' % sitio,
            'archivo': '/sitios/%s.conf' % sitio, 'servername': servername,
            'contenido': contenido, 'certificado': certificado, 'sockets_unix': []}


class CertificadoEnUso(Base):
    """Borrar un certificado que aún usa un vhost impide arrancar a Apache."""
    CERT = '/etc/letsencrypt/live/prueba.x.net/fullchain.pem'

    def setUp(self):
        super().setUp()
        self.instancia(certificado='prueba.x.net')
        vivo = os.path.join(self.tmp, 'live')
        os.makedirs(os.path.join(vivo, 'prueba.x.net'))
        parche = mock.patch.object(ap.certificados, 'RUTA_LIVE', vivo)
        parche.start()
        self.addCleanup(parche.stop)

    def propio(self, sitio, servername):
        return vhost(sitio, servername, 'Alias /static/ %s/pry/static/' % self.carpeta, self.CERT)

    def test_se_conserva_si_lo_usa_un_vhost_propio_que_no_se_borra(self):
        self.cargar_vhosts.return_value = [self.propio('viejo', 'otro.x.net')]
        plan = self.plan()
        self.assertIn(('vhost', 'viejo'), self.recursos(plan['conservar']))
        self.assertIn(('certificado', 'prueba.x.net'), self.recursos(plan['conservar']))

    def test_se_borra_si_solo_lo_usa_el_vhost_que_se_borra(self):
        self.cargar_vhosts.return_value = [self.propio('prueba.x.net', 'prueba.x.net')]
        self.assertIn(('certificado', 'prueba.x.net'), self.recursos(self.plan()['borrar']))

    def test_detecta_el_uso_en_el_contenido_aunque_no_sea_el_primer_certificado(self):
        self.cargar_vhosts.return_value = [
            vhost('ajeno', 'ajeno.x.net', 'SSLCertificateFile %s' % self.CERT, '/otro/fullchain.pem')]
        self.assertIn(('certificado', 'prueba.x.net'), self.recursos(self.plan()['conservar']))

    def test_vuelve_a_comprobar_justo_antes_de_borrar(self):
        """El vhost no se pudo quitar: sigue en disco y el certificado no se toca."""
        ap.registro_actualizar(self.config, 'prueba', base=None)
        self.cargar_vhosts.return_value = [self.propio('prueba.x.net', 'prueba.x.net')]
        tarea = mock.Mock()
        tarea.ejecutar.return_value = (0, '')
        with mock.patch.object(ap, '_quitar_vhost'), \
                mock.patch.object(ap.certificados, 'eliminar') as eliminar:
            with self.assertRaises(ap.TareaError) as error:
                ap.eliminar_instancia(tarea, self.config, mock.Mock(), 'prueba', con_backup=False)
        eliminar.assert_not_called()
        self.assertIn('certificado prueba.x.net', str(error.exception))


class SocketConservado(Base):
    def setUp(self):
        super().setUp()
        self.instancia(base=None)
        self.unidades = os.path.join(self.tmp, 'systemd')
        os.makedirs(self.unidades)
        for extension in ('service', 'socket'):
            open(os.path.join(self.unidades, 'prueba.%s' % extension), 'w').close()
        parche = mock.patch.object(ap, 'DIR_UNIDADES', self.unidades)
        parche.start()
        self.addCleanup(parche.stop)
        self.cargar_unidades.return_value = [{'unidad': 'prueba', 'workingdirectory': self.proyecto}]
        self.tarea = mock.Mock()
        self.tarea.ejecutar.return_value = (0, '')

    def existe(self, extension):
        return os.path.exists(os.path.join(self.unidades, 'prueba.%s' % extension))

    def eliminar(self):
        try:
            ap.eliminar_instancia(self.tarea, self.config, mock.Mock(), 'prueba', con_backup=False)
        except ap.TareaError:
            pass

    def test_no_borra_un_socket_que_el_plan_conserva(self):
        self.cargar_sockets.return_value = {'prueba': {'rutas': ['/run/otro.sock']}}
        self.eliminar()
        self.assertFalse(self.existe('service'))
        self.assertTrue(self.existe('socket'))
        comandos = [c[0][0] for c in self.tarea.ejecutar.call_args_list]
        self.assertNotIn(['systemctl', 'disable', '--now', 'prueba.socket'], comandos)

    def test_el_registro_sigue_si_se_conserva_el_socket(self):
        self.cargar_sockets.return_value = {'prueba': {'rutas': ['/run/otro.sock']}}
        self.eliminar()
        self.assertIsNotNone(ap.registro_obtener(self.config, 'prueba'))

    def test_borra_ambos_si_son_propios(self):
        self.cargar_sockets.return_value = {'prueba': {'rutas': ['/run/prueba.sock']}}
        self.eliminar()
        self.assertFalse(self.existe('service'))
        self.assertFalse(self.existe('socket'))


class NombreCertificado(unittest.TestCase):
    """H3: el nombre llegaba sin validar a rutas que se borran como root."""
    MALOS = ('../../etc/passwd', '/etc', '..', '.', 'a/b', 'a\\b', '-x', '--cert-name', '', None,
             'a b', 'a\nb', 'x' * 300)

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.base = os.path.join(self.tmp, 'letsencrypt')
        self.vivo = os.path.join(self.base, 'live')
        self.config = {'certbot_renewal_dir': os.path.join(self.base, 'renewal')}
        os.makedirs(self.vivo)
        os.makedirs(self.config['certbot_renewal_dir'])
        parche = mock.patch.object(cert, 'RUTA_LIVE', self.vivo)
        parche.start()
        self.addCleanup(parche.stop)
        parche = mock.patch.object(cert, 'ejecutar', return_value=(0, '', ''))
        self.ejecutar = parche.start()
        self.addCleanup(parche.stop)
        parche = mock.patch.object(cert.webserver, 'cargar_vhosts', return_value=[])
        self.cargar_vhosts = parche.start()
        self.addCleanup(parche.stop)

    def test_acepta_nombres_reales(self):
        for nombre in ('quimica.integrasoluc.net', 'x.net-0001', 'Mi_Cert'):
            self.assertTrue(cert.nombre_valido(nombre), nombre)

    def test_rechaza_nombres_peligrosos(self):
        for nombre in self.MALOS:
            self.assertFalse(cert.nombre_valido(nombre), repr(nombre))

    def test_eliminar_no_toca_nada_con_un_nombre_malo(self):
        victima = os.path.join(self.tmp, 'victima')
        os.makedirs(victima)
        for nombre in (victima, '../../victima'):
            self.assertFalse(cert.eliminar(nombre, self.config, forzar=True)['ok'])
        self.ejecutar.assert_not_called()
        self.assertTrue(os.path.isdir(victima))

    def test_pausar_y_reanudar_rechazan_un_nombre_malo(self):
        conf = os.path.join(self.tmp, 'otro.conf')
        open(conf, 'w').close()
        self.assertFalse(cert.pausar_renovacion(conf[:-5], self.config)['ok'])
        self.assertFalse(cert.reanudar_renovacion(conf[:-5], self.config)['ok'])
        self.assertTrue(os.path.exists(conf))

    def test_no_sigue_un_enlace_que_sale_de_letsencrypt(self):
        victima = os.path.join(self.tmp, 'victima')
        os.makedirs(victima)
        try:
            os.symlink(victima, os.path.join(self.vivo, 'x.net'), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('sin permiso para crear enlaces')
        self.assertFalse(cert.eliminar('x.net', self.config, forzar=True)['ok'])
        self.assertTrue(os.path.isdir(victima))

    def test_sigue_borrando_los_restos_propios(self):
        resto = os.path.join(self.vivo, 'x.net')
        os.makedirs(resto)
        self.assertTrue(cert.eliminar('x.net', self.config, forzar=True)['ok'])
        self.assertFalse(os.path.exists(resto))


class EliminarCertificadoEnUso(NombreCertificado):
    """Hereda el entorno y repite también las pruebas de nombre con este guardia activo."""
    CERT = '/etc/letsencrypt/live/x.net/fullchain.pem'

    def intentar(self, usado, forzar=False):
        resto = os.path.join(self.vivo, 'x.net')
        os.makedirs(resto, exist_ok=True)
        self.cargar_vhosts.return_value = [usado]
        resultado = cert.eliminar('x.net', self.config, forzar=forzar)
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['en_uso'], usado['nombre'])
        self.ejecutar.assert_not_called()
        self.assertTrue(os.path.isdir(resto))

    def test_bloquea_si_un_vhost_lo_usa(self):
        self.intentar(vhost('sitio', 'x.net', '', self.CERT))

    def test_bloquea_tambien_al_forzar(self):
        self.intentar(vhost('sitio', 'x.net', '', self.CERT), forzar=True)

    def test_bloquea_si_esta_en_el_contenido(self):
        self.intentar(vhost('sitio', 'y.net', 'SSLCertificateFile %s' % self.CERT, '/otro/cert.pem'))

    def test_no_confunde_un_nombre_que_empieza_igual(self):
        self.cargar_vhosts.return_value = [
            vhost('sitio', 'x.net.ec', '', '/etc/letsencrypt/live/x.net.ec/fullchain.pem')]
        self.assertIsNone(cert.en_uso('x.net', self.config))


class BaseAjena(Base):
    """H4: Reanudar podía borrar o reutilizar la base de otro cliente."""

    def setUp(self):
        super().setUp()
        for objetivo, valor in (('_credenciales_template', {'POSTGRES_USER': 'postgres'}),
                                ('_psql_valor', (True, 't')),
                                ('_base_incompleta', (False, 'resumen')),
                                ('_tamano_base', None), ('_tamano_carpeta', None), ('_libre', None)):
            parche = mock.patch.object(ap, objetivo, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)

    def errores(self, cliente='nueva', base='db_cliente', **extra):
        datos = dict(tipo='inventario', cliente=cliente, base=base, reanudar=True, **extra)
        return [r['mensaje'] for r in ap.validar(self.config, mock.Mock(), datos)
                if r['critico'] and 'no es de esta instancia' in r['mensaje']]

    def otra_instancia(self, base='db_cliente'):
        carpeta = os.path.join(self.home, 'cliente', 'pryinventario')
        os.makedirs(carpeta)
        self.credenciales(carpeta, base)

    def test_no_recrea_la_base_de_otra_instancia(self):
        self.otra_instancia()
        self.assertTrue(self.errores(recrear_base=True))

    def test_no_reutiliza_la_base_de_otra_instancia(self):
        self.otra_instancia()
        self.assertTrue(self.errores())

    def test_no_recrea_la_base_origen(self):
        self.assertTrue(self.errores(base='inventario_template', recrear_base=True))

    def test_no_recrea_una_base_sin_dueno_conocido(self):
        self.assertTrue(self.errores(recrear_base=True))

    def test_recrea_la_base_anotada_en_el_registro(self):
        ap.registro_crear(self.config, 'nueva', base='db_nueva')
        self.assertFalse(self.errores(base='db_nueva', recrear_base=True))

    def test_recrea_la_base_de_su_credenciales(self):
        carpeta = os.path.join(self.home, 'nueva', 'pryinventario')
        os.makedirs(carpeta)
        open(os.path.join(carpeta, 'manage.py'), 'w').close()
        self.credenciales(carpeta, 'db_nueva')
        self.assertFalse(self.errores(base='db_nueva', recrear_base=True))


class RecrearConCopia(Base):
    def setUp(self):
        super().setUp()
        self.instancia()
        self.tarea = mock.Mock()
        self.tarea.datos = dict(tipo='inventario', cliente='prueba', base='db_prueba', reanudar=True,
                                recrear_base=True, confirmar_base='db_prueba', crear_servicio=False,
                                crear_vhost=False)
        self.tarea.ejecutar.return_value = (0, '')
        previo = {'carpeta': self.carpeta, 'carpeta_existe': True, 'copia_completa': True,
                  'unidad': None, 'vhosts': [], 'db_credenciales': 'db_prueba'}
        for objetivo, valor in (('validar', []), ('_previo', previo), ('_contar_tablas', 5),
                                ('_credenciales_template', {'POSTGRES_PASSWORD': 'clave1'}),
                                ('_actualizar_credenciales', {})):
            parche = mock.patch.object(ap, objetivo, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)
        for objetivo, valor in (('getsize', 2048), ('isfile', True)):
            parche = mock.patch.object(ap.os.path, objetivo, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)

    def comandos(self):
        return [' '.join(map(str, c[0][0])) for c in self.tarea.ejecutar.call_args_list]

    def crear(self):
        with mock.patch.object(ap.os, 'makedirs'), mock.patch.object(ap.os, 'remove'):
            ap.crear_instancia(self.tarea, self.config, mock.MagicMock())

    def test_sin_confirmacion_no_borra(self):
        self.tarea.datos['confirmar_base'] = 'otra'
        with mock.patch.object(ap.mod_backups, 'respaldar_base') as copia:
            with self.assertRaises(ap.TareaError):
                self.crear()
        copia.assert_not_called()
        self.assertFalse([c for c in self.comandos() if 'DROP DATABASE' in c])

    def test_si_falla_la_copia_no_borra(self):
        with mock.patch.object(ap.mod_backups, 'respaldar_base',
                               return_value={'ok': False, 'error': 'sin espacio'}):
            with self.assertRaises(ap.TareaError):
                self.crear()
        self.assertFalse([c for c in self.comandos() if 'DROP DATABASE' in c])

    def test_copia_antes_de_borrar(self):
        orden = []

        def ejecutar(comando, **_opciones):
            orden.append(' '.join(map(str, comando)))
            return 0, ''

        def copiar(*_argumentos):
            orden.append('COPIA')
            return {'ok': True, 'archivo': 'x'}

        self.tarea.ejecutar.side_effect = ejecutar
        with mock.patch.object(ap.mod_backups, 'respaldar_base', side_effect=copiar):
            self.crear()
        borrar = [i for i, c in enumerate(orden) if 'DROP DATABASE' in c]
        self.assertTrue(borrar)
        self.assertLess(orden.index('COPIA'), borrar[0])


@unittest.skipUnless(HAY_FLASK, 'Flask no está instalado')
class Rutas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.config import cargar
        from app.webapp import crear_app
        cls.tmp = tempfile.mkdtemp()
        config = cargar(os.path.join(cls.tmp, 'no-existe.json'))
        config['auth']['enabled'] = False
        config['notificaciones'] = dict(config.get('notificaciones') or {}, enabled=False)
        cls.config = config
        cls.app = crear_app(config)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.cliente = self.app.test_client()

    def sin_admin(self):
        self.config['auth']['enabled'] = True
        self.addCleanup(self.config['auth'].__setitem__, 'enabled', False)
        with self.cliente.session_transaction() as sesion:
            sesion.update(usuario='lector', gestionar_excluidos=False, ver_excluidos=False)

    def test_accion_certificado_rechaza_nombre_malo(self):
        with mock.patch('app.webapp.mod_certificados.eliminar') as eliminar:
            for nombre in ('../../etc', '/etc/passwd', '-x'):
                respuesta = self.cliente.post('/api/certificados/accion', json={
                    'nombre': nombre, 'accion': 'eliminar', 'forzar': True})
                self.assertEqual(respuesta.status_code, 400, nombre)
        eliminar.assert_not_called()

    def eliminar_certificado(self):
        return self.cliente.post('/api/certificados/accion', json={
            'nombre': 'x.net', 'accion': 'eliminar'})

    def test_eliminar_certificado_requiere_administrador(self):
        self.sin_admin()
        with mock.patch('app.webapp.mod_certificados.eliminar') as eliminar:
            self.assertEqual(self.eliminar_certificado().status_code, 403)
        eliminar.assert_not_called()

    def test_pausar_certificado_sigue_sin_requerir_administrador(self):
        self.sin_admin()
        with mock.patch('app.webapp.mod_certificados.pausar_renovacion',
                        return_value={'ok': True}):
            respuesta = self.cliente.post('/api/certificados/accion', json={
                'nombre': 'x.net', 'accion': 'pausar'})
        self.assertEqual(respuesta.status_code, 200)

    def test_eliminar_certificado_en_uso_responde_error(self):
        usado = vhost('sitio', 'x.net', '', '/etc/letsencrypt/live/x.net/fullchain.pem')
        with mock.patch('app.certificados.webserver.cargar_vhosts', return_value=[usado]), \
                mock.patch('app.certificados.ejecutar') as ejecutar:
            respuesta = self.eliminar_certificado()
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('sitio.conf', respuesta.get_json()['error'])
        ejecutar.assert_not_called()

    def test_renovar_rechaza_nombre_malo(self):
        with mock.patch('app.webapp.mod_certificados.renovar') as renovar:
            respuesta = self.cliente.post('/api/certificados/renovar',
                                          json={'nombre': '--config-dir=/tmp'})
        self.assertEqual(respuesta.status_code, 400)
        renovar.assert_not_called()

    def alta_recrear(self, **extra):
        datos = dict(tipo='inventario', cliente='nueva', base='db_nueva', reanudar=True,
                     recrear_base=True, simular=True)
        datos.update(extra)
        with mock.patch('app.webapp.aprovisionar.crear_instancia'):
            return self.cliente.post('/api/aprovisionar/crear', json=datos)

    def test_recrear_requiere_administrador(self):
        self.sin_admin()
        self.assertEqual(self.alta_recrear().status_code, 403)

    def test_recrear_real_requiere_confirmar_el_nombre(self):
        self.assertEqual(self.alta_recrear(simular=False).status_code, 400)
        self.assertEqual(self.alta_recrear(simular=False, confirmar_base='otra').status_code, 400)

    def test_recrear_confirmado_se_lanza(self):
        self.assertEqual(self.alta_recrear(simular=False, confirmar_base='db_nueva').status_code, 202)


if __name__ == '__main__':
    unittest.main()
