# integrasolucadminvps

Panel web ligero para administrar y monitorear, **en tiempo real**, todas las
instancias de **pryinventario** y **pryrestaurante** instaladas en el VPS.

Se instala en el propio servidor, sale por **IP pública + puerto** y no usa base
de datos propia: cada consulta se hace en el momento contra systemd, Apache,
los certificados y (opcionalmente) la base PostgreSQL de cada cliente.

---

## Qué muestra de cada instancia

| Dato | De dónde sale |
|---|---|
| Cliente, sistema (inventario/restaurante) y ruta de instalación | Escaneo de `/home/*/pryinventario` y `/home/*/pryrestaurante` |
| URL pública y si responde (código HTTP) | **ServerName del vhost de Apache** (con `DOMINIO_GENERAL` como respaldo) + petición en vivo |
| Aviso de `DOMINIO_GENERAL` desactualizado | Comparación entre `credenciales.json` y el vhost |
| Certificado SSL: vigente / por vencer / vencido, días restantes, emisor | `SSLCertificateFile` del vhost o `/etc/letsencrypt/live/<dominio>/` |
| Servicio systemd: activo, arranque, PID, memoria, uptime, puerto de gunicorn | El `.service` que apunta a la carpeta (`WorkingDirectory`/`ExecStart`) + `systemctl show` |
| Vhost de Apache: archivo, sitio, si está habilitado, ServerName, proxy | `/etc/apache2/sites-available` + `sites-enabled` |
| Fecha de creación de la instancia y del archivo `.service` | `stat` de la carpeta y del unit file |
| Base de datos: activa/caída, tamaño, versión, tablas más grandes | PostgreSQL de la instancia (`credenciales.json`) |
| Tamaño de la carpeta `media` | `du -sb` con caché |
| Última auditoría (fecha, hora, usuario, tabla y acción) | `seguridad_audiusuariotabla` |
| Facturas emitidas: total, del mes actual, del mes anterior, último mes facturado y meses sin facturar | `facturacion_facturareal` |
| Última sesión y sesiones vigentes | `auth_user.last_login`, `django_session`, `seguridad_usuarioconectado` |
| Primera y última venta | `salida_salidaservicios` / `pedido_venta` y `facturacion_facturareal` |
| Rama y último commit desplegado | `git log` de la instalación |

Las tablas de ventas **se detectan automáticamente** (`information_schema`), por
eso funciona aunque el esquema varíe entre inventario y restaurante.

### Cómo identifica cada cosa

- **El servicio** no se adivina por el nombre del cliente: se leen los `.service`
  de `/etc/systemd/system` y se toma el que tenga `WorkingDirectory` (o
  `ExecStart`) apuntando a esa carpeta. Así aparecen también los servicios que
  se llaman distinto al cliente.
- **El vhost de Apache** se empareja por el puerto al que hace `ProxyPass`
  (el mismo del gunicorn) y por la ruta de la instalación dentro del archivo.
  Si no hay una señal fuerte se muestra "sin vhost" en vez de atribuirle a un
  cliente el vhost de otro. Los `000-default*` quedan descartados.
- **El dominio** sale del `ServerName` de ese vhost, porque el
  `DOMINIO_GENERAL` de `credenciales.json` casi siempre queda con el valor del
  template al clonar la instancia. Cuando ambos difieren, el panel lo marca
  como "dominio desactualizado" (columna URL y tarjeta del resumen).
- **El certificado** se lee del `SSLCertificateFile` del vhost o de
  `/etc/letsencrypt/live/<dominio>/`. Si el emisor es igual al sujeto (o es el
  `ssl-cert-snakeoil` de Apache) se marca **autofirmado**, para que no parezca
  un certificado válido de 10 años.

## Qué permite hacer

- Iniciar, detener, reiniciar, habilitar o deshabilitar el **servicio systemd**.
- Activar o desactivar el **sitio de Apache** (`a2ensite` / `a2dissite` + reload).
- Buscar por cliente, empresa, dominio, ruta, servicio o base de datos.
- Ordenar por cualquier columna: fecha de implementación, tamaño de la base,
  tamaño de media, última venta, meses sin facturar, etc.
- Mostrar u ocultar grupos de columnas (Instancia / Servicio y web / Base y
  archivos / Actividad) y filtrar con un clic desde las tarjetas del resumen.
- Exportar el listado completo a **Excel (.xlsx)** con formato y a **CSV**.
- Ver el **historial** de acciones ejecutadas desde el panel (`var/acciones.log`).
- Consultar todo desde consola (`run.py --reporte`) o por **API JSON**.

Todas las acciones pasan por una lista blanca de comandos y quedan registradas
con el usuario que las ejecutó.

---

## Instalación

```bash
cd /opt
git clone https://github.com/hllerenaa/integrasolucadminvps
cd integrasolucadminvps
sudo ./install.sh --port 8600 --usuario admin --password "TuClaveSegura"
```

El instalador:

1. Crea el entorno virtual e instala dependencias (Flask, waitress, psycopg2, openpyxl).
2. Genera `config.json` con la clave guardada como hash SHA-256 (permisos 600).
3. Instala y arranca el servicio `integrasolucadmin` en systemd.
4. Abre el puerto en `ufw` si está activo.

Luego se entra desde `http://<IP-PUBLICA>:8600`.

Opciones: `--bind`, `--servicio <nombre>`, `--sin-servicio`, `--help`.

### Cambiar usuario o clave del panel

```bash
cd /opt/integrasolucadminvps
venv/bin/python run.py --usuario admin --clave "TuNuevaClave"
systemctl restart integrasolucadmin
```

La clave se guarda como hash SHA-256 en `config.json` (permisos 600); nunca en texto plano.

### Actualizar

```bash
cd /opt/integrasolucadminvps && sudo ./update.sh
```

### Desinstalar el servicio

```bash
sudo ./uninstall.sh
```

---

## Configuración (`config.json`)

| Clave | Para qué sirve |
|---|---|
| `host`, `port` | Dirección y puerto de escucha (`0.0.0.0` = accesible por IP pública) |
| `base_dirs`, `profundidad` | Dónde buscar instancias (por defecto `/home`, 2 niveles) |
| `proyectos` | Carpetas que identifican cada sistema (`pryinventario`, `pryrestaurante`) |
| `excluir_clientes` | Carpetas a ignorar (`old`, `template`, …) |
| `servicio_patron` | Cómo se llama el servicio systemd; por defecto `{cliente}` |
| `overrides` | Por cliente: `servicio`, `dominio` o `ignorar: true` |
| `consultar_bd` | `false` deja el panel 100 % sin tocar PostgreSQL |
| `medir_media` | `false` desactiva el cálculo del tamaño de `media` |
| `verificar_url` | `false` no golpea las URLs públicas |
| `apache_dirs` | Directorios de vhosts si no son los de Debian/Ubuntu |
| `intervalo_refresco` | Segundos entre refrescos automáticos en segundo plano |
| `acciones` | `enabled`, `servicios`, `apache`: permite o bloquea cada tipo de acción |
| `auth` | `username` + `password_hash` (o `password`) y `api_token` opcional |

> Si sólo quieres consulta de servicios, Apache, SSL y URL —sin nada de base de
> datos— pon `"consultar_bd": false`. El panel oculta esas columnas solo.

---

## Uso desde consola

```bash
venv/bin/python run.py --reporte    # tabla con el estado de todas las instancias
venv/bin/python run.py --json       # volcado completo en JSON
venv/bin/python run.py --dev        # servidor de desarrollo
venv/bin/python run.py --usuario admin --clave "..."   # cambia las credenciales
```

`--reporte` devuelve código de salida 1 si hay servicios caídos, bases
inaccesibles o certificados vencidos: sirve para cron o alertas.

## API

| Endpoint | Descripción |
|---|---|
| `GET /api/estado` | Estado de todas las instancias + resumen |
| `GET /api/instancia/<cliente\|tipo>` | Detalle de una instancia |
| `POST /api/refrescar` | Fuerza un refresco (`{"solo": "id", "media": true}`) |
| `POST /api/accion` | `{"id": "...", "accion": "reiniciar"}` |
| `GET /api/acciones` | Historial de acciones |
| `GET /export.xlsx`, `GET /export.csv` | Exportaciones (aceptan `?tipo=` y `?q=`) |
| `GET /healthz` | Chequeo de salud (sin autenticación) |

Acciones disponibles: `iniciar`, `detener`, `reiniciar`, `habilitar`,
`deshabilitar`, `apache_activar`, `apache_desactivar`, `apache_recargar`.

Para scripts se puede usar `auth.api_token` con la cabecera `X-API-Token`:

```bash
curl -H "X-API-Token: TU_TOKEN" http://IP:8600/api/estado
```

---

## Notas de operación

- El panel corre como **root** porque necesita `systemctl`, leer los
  `credenciales.json` de cada instancia y los certificados.
- **No guarda credenciales**: lee el `credenciales.json` de cada instalación en
  cada consulta y nunca expone las contraseñas por la API ni en las exportaciones.
- No usa base de datos propia. En `var/` sólo quedan la caché del tamaño de
  `media`, la clave de sesión y el log de acciones.
- Recomendado: exponer el puerto sólo a IPs conocidas
  (`ufw allow from <tu-ip> to any port 8600`) o publicarlo detrás de Apache con SSL.
