# -*- coding: utf-8 -*-
"""Pruebas de lo que no necesita un servidor: dominio, plantillas, registro y protecciones.

Ejecutar con:  python -m unittest discover -s tests
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from app import aprovisionar as ap


class ConfigPrueba(dict):
    var_dir = None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(self.home)
        self.config = ConfigPrueba(
            base_dirs=[self.home], proyectos={'inventario': 'pryinventario'},
            aprovisionamiento={'base_dir': self.home, 'templates': {'inventario': {
                'ruta': os.path.join(self.home, 'template', 'pryinventario'),
                'db_origen': 'inventario_template'}}})
        self.config.var_dir = os.path.join(self.tmp, 'var')
        self.carpeta = os.path.join(self.home, 'prueba')
        self.proyecto = os.path.join(self.carpeta, 'pryinventario')
        for nombre, valor in (('cargar_unidades', []), ('cargar_sockets', {})):
            parche = mock.patch.object(ap.units, nombre, return_value=valor)
            setattr(self, nombre, parche.start())
            self.addCleanup(parche.stop)
        parche = mock.patch.object(ap.webserver, 'cargar_vhosts', return_value=[])
        self.cargar_vhosts = parche.start()
        self.addCleanup(parche.stop)
        parche = mock.patch.object(ap, '_existe_base', return_value=True)
        parche.start()
        self.addCleanup(parche.stop)

    def instancia(self, base='db_prueba', credenciales='db_prueba', **campos):
        os.makedirs(self.proyecto, exist_ok=True)
        self.credenciales(self.proyecto, credenciales)
        ap.registro_crear(self.config, 'prueba', tipo='inventario', carpeta=self.carpeta,
                          proyecto='pryinventario', dominio='prueba.x.net', base=base,
                          unidad='prueba', socket='prueba', sitio='prueba.x.net', **campos)

    @staticmethod
    def credenciales(carpeta, base):
        with open(os.path.join(carpeta, 'credenciales.json'), 'w', encoding='utf-8') as fh:
            json.dump({'POSTGRES_DBNAME': base}, fh)

    def plan(self):
        return ap.plan_eliminacion(self.config, 'prueba')

    @staticmethod
    def recursos(items):
        return {(i['recurso'], i['valor']) for i in items}


class Dominio(unittest.TestCase):
    def test_limpia_urls(self):
        for entrada, esperado in (
                ('https://admin.integrasoluc.net/nueva', 'admin.integrasoluc.net'),
                ('HTTP://Admin.Integrasoluc.NET:8080/a?b=1#c', 'admin.integrasoluc.net'),
                ('  usuario:clave@admin.x.net/ ', 'admin.x.net'),
                ('admin.x.net.', 'admin.x.net'),
                ('admin.x.net', 'admin.x.net'),
                ('', ''), (None, '')):
            self.assertEqual(ap.normalizar_dominio(entrada), esperado, entrada)


class Plantillas(unittest.TestCase):
    def test_vhost_http_usa_socket_y_no_ssl(self):
        texto = ap.texto_vhost('quimica', 'quimica.x.net', '/home/quimica/pryinventario')
        self.assertIn('ProxyPass / unix:/run/quimica.sock|http://localhost/', texto)
        self.assertIn('Alias /static/ /home/quimica/pryinventario/static/', texto)
        self.assertIn('${APACHE_LOG_DIR}/quimica_error.log', texto)
        self.assertNotIn('SSLCertificateFile', texto)
        self.assertNotIn('__', texto)

    def test_vhost_ssl_es_un_solo_archivo_con_redireccion(self):
        texto = ap.texto_vhost('quimica', 'quimica.x.net', '/home/quimica/pryinventario', ssl=True)
        self.assertIn('<VirtualHost *:80>', texto)
        self.assertIn('<VirtualHost *:443>', texto)
        self.assertIn('R=permanent', texto)
        self.assertIn('/etc/letsencrypt/live/quimica.x.net/fullchain.pem', texto)
        self.assertNotIn('__', texto)

    def test_servicio_y_socket_como_produccion(self):
        servicio = ap.texto_servicio('quimica', '/home/quimica/pryinventario',
                                     '/home/integrasoluc/venv', 'pryinventario')
        self.assertIn('Requires=quimica.socket', servicio)
        self.assertIn('/home/integrasoluc/venv/bin/gunicorn --workers 1 --threads 4 --timeout 300 '
                      '--bind unix:/run/quimica.sock pryinventario.wsgi:application', servicio)
        self.assertIn('ListenStream=/run/quimica.sock', ap.texto_socket('quimica'))


class Registro(Base):
    def test_se_escribe_recurso_por_recurso(self):
        ap.registro_crear(self.config, 'prueba', carpeta=self.carpeta)
        self.assertIsNone(ap.registro_obtener(self.config, 'prueba')['base'])
        ap.registro_actualizar(self.config, 'prueba', base='db_prueba')
        self.assertEqual(ap.registro_obtener(self.config, 'prueba')['base'], 'db_prueba')

    def test_actualizar_no_vuelve_eliminable_una_instancia_ajena(self):
        ap.registro_actualizar(self.config, 'antigua', base='db_antigua')
        self.assertIsNone(ap.registro_obtener(self.config, 'antigua'))
        self.assertIsNone(ap.plan_eliminacion(self.config, 'antigua'))


class Protecciones(Base):
    def test_borra_lo_propio(self):
        self.instancia()
        plan = self.plan()
        self.assertIn(('carpeta', self.carpeta), self.recursos(plan['borrar']))
        self.assertIn(('base', 'db_prueba'), self.recursos(plan['borrar']))
        self.assertEqual(plan['conservar'], [])

    def test_no_borra_carpeta_fuera_de_base_dir(self):
        self.instancia()
        fuera = os.path.join(self.tmp, 'otro', 'prueba')
        os.makedirs(fuera)
        ap.registro_actualizar(self.config, 'prueba', carpeta=fuera)
        plan = self.plan()
        self.assertIn(('carpeta', fuera), self.recursos(plan['conservar']))
        self.assertFalse([i for i in plan['borrar'] if i['recurso'] == 'carpeta'])

    def test_no_borra_carpeta_de_un_template(self):
        self.assertFalse(ap._carpeta_permitida(self.config, os.path.join(self.home, 'template')))

    def test_no_borra_enlace_a_otra_carpeta(self):
        real = os.path.join(self.tmp, 'produccion')
        os.makedirs(real)
        enlace = os.path.join(self.home, 'enlace')
        try:
            os.symlink(real, enlace, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('sin permiso para crear enlaces')
        self.assertFalse(ap._carpeta_permitida(self.config, enlace))

    def test_no_borra_base_de_otra_instancia(self):
        self.instancia()
        otra = os.path.join(self.home, 'otra', 'pryinventario')
        os.makedirs(otra)
        self.credenciales(otra, 'db_prueba')
        plan = self.plan()
        self.assertIn(('base', 'db_prueba'), self.recursos(plan['conservar']))
        self.assertFalse(plan['con_base'])

    def test_no_borra_base_origen(self):
        self.instancia(base='inventario_template', credenciales='inventario_template')
        self.assertIn(('base', 'inventario_template'), self.recursos(self.plan()['conservar']))

    def test_no_borra_base_si_credenciales_apunta_a_otra(self):
        self.instancia(credenciales='db_produccion')
        self.assertIn(('base', 'db_prueba'), self.recursos(self.plan()['conservar']))

    def test_no_borra_servicio_ajeno(self):
        self.instancia()
        self.cargar_unidades.return_value = [{'unidad': 'prueba', 'workingdirectory': '/home/otro/x'}]
        self.cargar_sockets.return_value = {'prueba': {'rutas': ['/run/prueba.sock']}}
        plan = self.plan()
        self.assertIn(('servicio', 'prueba'), self.recursos(plan['conservar']))
        self.assertIn(('socket', 'prueba'), self.recursos(plan['conservar']))

    def test_borra_servicio_y_socket_propios(self):
        self.instancia()
        self.cargar_unidades.return_value = [{'unidad': 'prueba', 'workingdirectory': self.proyecto}]
        self.cargar_sockets.return_value = {'prueba': {'rutas': ['/run/prueba.sock']}}
        borrar = self.recursos(self.plan()['borrar'])
        self.assertIn(('servicio', 'prueba'), borrar)
        self.assertIn(('socket', 'prueba'), borrar)

    def test_vhost_con_el_mismo_nombre_de_otro_cliente_no_es_propio(self):
        self.instancia()
        self.cargar_vhosts.return_value = [{
            'servidor': 'apache', 'sitio': 'prueba.x.net', 'archivo': '/x/prueba.x.net.conf',
            'servername': 'prueba.x.net', 'contenido': 'Alias /static/ /home/otro/pry/static/',
            'sockets_unix': ['/run/otro.sock']}]
        self.assertFalse([i for i in self.plan()['borrar'] if i['recurso'] == 'vhost'])

    def test_nombre_parecido_no_es_propio(self):
        vhost = {'contenido': 'Alias /static/ /home/quimica2/pry/static/', 'sockets_unix': ['/run/quimica2.sock']}
        self.assertFalse(ap._vhost_propio(vhost, 'quimica', '/home/quimica'))

    def test_no_borra_certificado_que_usa_otro_sitio(self):
        self.instancia(certificado='prueba.x.net')
        self.cargar_vhosts.return_value = [{
            'certificado': '/etc/letsencrypt/live/prueba.x.net/fullchain.pem',
            'nombre': 'otro.conf', 'contenido': '/home/otro/', 'sockets_unix': []}]
        self.assertIn(('certificado', 'prueba.x.net'), self.recursos(self.plan()['conservar']))


class ValidarCampo(Base):
    def test_avisa_si_otro_sitio_usa_el_dominio(self):
        self.cargar_vhosts.return_value = [{
            'servername': 'admin.x.net', 'alias': [], 'nombre': 'admin.x.net.conf',
            'contenido': 'Alias /static/ /home/otro/pry/static/', 'sockets_unix': []}]
        resultado = ap.validar_campo(self.config, 'dominio', {
            'valor': 'https://admin.x.net/nueva', 'cliente': 'prueba'})
        self.assertEqual(resultado['nivel'], 'error')
        self.assertIn('admin.x.net.conf', resultado['mensaje'])
        self.assertEqual(resultado['valor'], 'admin.x.net')

    def test_rechaza_carpeta_ocupada(self):
        os.makedirs(self.carpeta)
        resultado = ap.validar_campo(self.config, 'cliente', {'valor': 'prueba'})
        self.assertEqual(resultado['nivel'], 'error')


class Eliminar(Base):
    def setUp(self):
        super().setUp()
        self.instancia(base=None)
        self.tarea = mock.Mock()
        self.colector = mock.Mock()

    def test_borra_y_quita_del_registro(self):
        ap.eliminar_instancia(self.tarea, self.config, self.colector, 'prueba', con_backup=False)
        self.assertFalse(os.path.exists(self.carpeta))
        self.assertIsNone(ap.registro_obtener(self.config, 'prueba'))

    def test_si_queda_algo_sigue_en_el_registro(self):
        with mock.patch.object(ap, '_borrar_carpeta', return_value=False):
            with self.assertRaises(ap.TareaError):
                ap.eliminar_instancia(self.tarea, self.config, self.colector, 'prueba', con_backup=False)
        self.assertIsNotNone(ap.registro_obtener(self.config, 'prueba'))

    def test_si_falla_la_copia_no_borra_nada(self):
        ap.registro_actualizar(self.config, 'prueba', base='db_prueba')
        self.credenciales(self.proyecto, 'db_prueba')
        with mock.patch.object(ap, '_credenciales_template', return_value={}), \
                mock.patch.object(ap.mod_backups, 'respaldar_base',
                                  return_value={'ok': False, 'error': 'sin espacio'}), \
                mock.patch.object(ap, '_borrar_base') as borrar_base:
            with self.assertRaises(ap.TareaError):
                ap.eliminar_instancia(self.tarea, self.config, self.colector, 'prueba')
        borrar_base.assert_not_called()
        self.assertTrue(os.path.exists(self.carpeta))

    def test_no_elimina_lo_que_no_es_del_panel(self):
        with self.assertRaises(ap.TareaError):
            ap.eliminar_instancia(self.tarea, self.config, self.colector, 'antigua')


if __name__ == '__main__':
    unittest.main()
