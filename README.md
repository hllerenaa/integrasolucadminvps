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
| Certificado SSL: vigente / por vencer / vencido / autofirmado, **fecha de vencimiento**, días restantes y emisor | `SSLCertificateFile` (Apache) o `ssl_certificate` (nginx) del sitio, o `/etc/letsencrypt/live/<dominio>/` |
| Servicio systemd: activo, arranque, PID, uptime, puerto de gunicorn | El `.service` que apunta a la carpeta (`WorkingDirectory`/`ExecStart`) + `systemctl show` |
| **Activación por socket**: si el `.service` está parado pero su `.socket` sigue escuchando (el sitio responde igual) | La unidad `<cliente>.socket` y su `ListenStream` |
| **Búsqueda de cédula por API** activada o no en cada sistema | `seguridad_configuracion` (`usar_api_persona` en inventario, `traer_api_cliente` en restaurante; la columna se detecta) |
| **RUC del proveedor** (`rucproveedor`), editable desde el panel, indicando además qué bases todavía no tienen el campo | `seguridad_configuracion.rucproveedor` |
| **Días sin uso**: cuánto lleva la instancia sin actividad real | La más reciente entre la última auditoría, el último inicio de sesión y la última venta |
| **Consumo de CPU y RAM de cada instancia**, con barra y % sobre el total del servidor | `CPUUsageNSec` (diferencia entre dos muestras) y `MemoryCurrent` de su unidad |
| **Cuánto ocupa cada instancia frente al disco**: BD, media y logs en % del disco | Tamaños medidos + `statvfs` del servidor |
| Sitio web: si es **Apache o nginx**, archivo, si está habilitado, ServerName, proxy, y si el demonio (`apache2`/`nginx`) está activo | `/etc/apache2/sites-*` y `/etc/nginx/sites-*` + `conf.d` |
| Fecha de creación de la instancia y del archivo `.service` | `stat` de la carpeta y del unit file |
| Base de datos: activa/caída, tamaño, versión, tablas más grandes | PostgreSQL de la instancia (`credenciales.json`) |
| Tamaño de la carpeta `media` | `du -sb` con caché |
| Tamaño de los archivos de log (proyecto, Apache y gunicorn, con sus rotaciones) | `*.log` de la instalación, `ErrorLog`/`CustomLog` del vhost y `--access-logfile`/`--error-logfile` del `.service` |
| Contenido de `credenciales.json` | Lectura directa del archivo, con las claves enmascaradas |
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
- **El sitio web** se busca en Apache y en nginx: se empareja por el puerto o el
  socket al que hace `ProxyPass` / `proxy_pass` (el mismo del gunicorn), por la
  ruta de la instalación dentro del archivo y, si no hay ninguna de esas señales,
  por el `DOMINIO_GENERAL` de `credenciales.json` cuando ese dominio es
  exclusivo de esa instalación. **Nunca se asigna el vhost de otro cliente por
  parecido de nombre**: antes se deja "sin vhost" (si no, `drones` se quedaba
  con el sitio de `dronesjv`). Cuando varias instalaciones comparten el mismo
  `DOMINIO_GENERAL` —el del template— ese dominio deja de servir como señal y
  la instancia se marca con "dominio compartido", que es justo lo que hay que
  corregir en su `credenciales.json`.
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

## Secciones del panel

| Sección | Para qué |
|---|---|
| **Instancias** (`/`) | El listado completo con todos los datos y las acciones sobre cada instalación |
| **Certificados** (`/certificados`) | Certificados de Let's Encrypt: emisión, vencimiento, renovar, pausar la renovación o eliminar, con buscador y tabla ordenable |
| **Backups** (`/backups`) | Respaldos por instancia o de cualquier base, descarga, retención y las bases del servidor sin instancia |
| **Nueva instancia** (`/nueva`) | El asistente de creación |
| **Excluidos** (`/excluidos`) | El mismo panel con todas las instalaciones y el interruptor para ocultarlas o mostrarlas |

## Usuarios y permisos

`config.json` → `auth`: el usuario histórico (`username`) más una lista `usuarios`.
Cada uno tiene dos permisos:

- `ver_excluidos`: ve también las instancias ocultas, marcadas, en el panel principal.
- `gestionar_excluidos`: puede entrar a `/excluidos` y cambiar la lista.

```bash
# usuario que ve todo menos lo excluido y no administra la lista
venv/bin/python run.py --usuario mochoa --clave "..."

# usuario que ve todo, incluidas las instancias ocultas
venv/bin/python run.py --usuario hllerenaa --clave "..." --ver-excluidos --gestionar-excluidos

venv/bin/python run.py --listar-usuarios
venv/bin/python run.py --quitar-usuario mochoa
```

## Backups

- Respaldo por instancia, de todas a la vez o de **cualquier base suelta**, con
  `pg_dump` + `gzip`, en `backups.destino` (por defecto `/home/backups/<cliente>/`)
  y con retención configurable (`backups.retencion`).
- Lee además tus repositorios existentes (`backups.repositorios`, p. ej.
  `/home/db_repository` de `backupall.sh`) reconociendo el formato
  `<base>_<AAAAMMDD>.zip`: se listan y se descargan, pero no se borran desde el panel.
- Avisa qué instancias no tienen backup o lo tienen atrasado, marca los dumps
  sospechosamente pequeños y permite descargarlos o borrarlos.
- Lista **todas las bases de PostgreSQL** del servidor con su tamaño y marca las
  que no corresponden a ninguna instalación, para respaldarlas antes de darlas de baja.

## Qué permite hacer

- **Abrir y editar el `credenciales.json`** de cada instancia desde el detalle
  (botón "Ver credenciales.json"): se muestra con las contraseñas ocultas,
  hay un botón para revelarlas y se puede guardar. Cada guardado deja un
  respaldo `credenciales.json.bak-<fecha>` (se conservan los 8 últimos),
  valida el JSON, conserva los permisos del archivo y queda registrado en el
  historial. Si editas sin revelar las claves, los valores enmascarados se
  conservan tal cual.
- Iniciar, detener, reiniciar, habilitar o deshabilitar el **servicio systemd**.
  Cuando la instancia usa activación por socket, cada acción se aplica al
  `.socket` **y** al `.service` (igual que `desactivar_sistemas.sh`) y después se
  verifica con `systemctl is-active` que ninguno quedó escuchando: detener sólo
  el servicio deja el socket activo y la primera petición lo vuelve a levantar.
- Activar o desactivar la **búsqueda de personas por cédula (API)** de cada
  instancia, con la columna que corresponda a su versión.
- Editar el **RUC del proveedor** (`rucproveedor`) de cada instancia desde el
  detalle. Si la base todavía no tiene la columna, el panel lo dice en vez de
  fallar, y hay un filtro para ver justo esas instalaciones pendientes de migrar.
- Ver y renovar los **certificados de Let's Encrypt** (botón *Certificados*):
  lista `certbot certificates` con fecha de emisión, de vencimiento, días
  restantes y a qué instancia pertenece cada uno, con renovación por
  certificado o de todos, y una opción de prueba (`--dry-run`). La renovación
  corre en segundo plano con log en vivo y recarga el servidor web al terminar.
  También se puede **pausar la renovación automática** de un certificado
  (reversible: se renombra su archivo en `/etc/letsencrypt/renewal`) o
  **eliminarlo**; tras `certbot delete` se comprueba que realmente desapareció
  y, si quedan restos en disco, se ofrece borrarlos.
- Activar o desactivar el **sitio web**: `a2ensite`/`a2dissite` en Apache o el
  enlace en `sites-enabled` en nginx. Si `a2dissite` falla (por ejemplo cuando
  el archivo está directamente en `sites-enabled` en vez de ser un enlace), el
  panel lo resuelve él mismo: quita el enlace o mueve el archivo a
  `sites-available`. Antes de recargar valida la configuración
  (`apache2ctl configtest` / `nginx -t`) y sólo recarga si pasa; la respuesta
  incluye la salida exacta de cada comando.
- Ver de un vistazo el consumo: columnas **CPU**, **RAM** y **Ocupa (BD+media+logs)**
  con barras, y tarjetas con la RAM, la carga y el disco del servidor indicando
  cuánto de eso se llevan las instancias.
- Buscar por cliente, empresa, RUC, dominio, ruta, servicio o base de datos, y
  filtrar por sistema, **estado del servicio** (activo, inactivo, fallido, sin
  unidad o parado con el socket escuchando), estado general, **búsqueda de cédula
  por API** (activa / desactivada / sin la opción) y **tiempo sin uso** (más de
  30, 60, 90, 180 días o un año).
- La tabla es compacta: el cliente lleva su empresa y las fechas de implementación
  y del servicio; la columna de URL lleva el dominio, la respuesta y la ruta de
  instalación. Esas columnas siguen disponibles en el selector "Ordenar por".
- Desplazar la tabla en horizontal **arrastrándola con el ratón** o con la barra
  que aparece encima de la tabla, sincronizada con ella.
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

## Crear una instancia nueva desde el panel

Botón **+ Nueva instancia**. Reproduce lo que hacen `new_instance_inventario.sh` y
`new_instance_restaurante.sh`, y además deja hecho lo que allí quedaba manual.

1. **Formulario**: sistema, nombre de la instancia, base, dominio y puerto de
   gunicorn (se sugiere el primero libre). La base y el dominio se autocompletan
   a partir del nombre. Se elige si el `.service` y el vhost se **clonan de una
   instancia que ya funciona** o se generan con las plantillas de `deploy/plantillas/`.
2. **Validar**: comprueba nombre, que la carpeta esté libre, que la base no
   exista, que el template esté disponible, que no haya otro servicio con ese
   nombre, que el puerto esté libre y que el dominio no esté en otro vhost.
3. **Simular**: hace las validaciones y muestra los comandos exactos que se
   ejecutarían, **sin tocar nada**. Conviene usarlo la primera vez.
4. **Crear instancia**: corre en segundo plano con log en vivo:
   actualizar el template desde git (opcional) → `pg_dump` de la base origen sin
   `django_migrations` → copiar el template (excluyendo `media/backups`, `__pycache__`,
   `*.pyc`, `*.log`) → permisos → `CREATE DATABASE` + restore → ajustar
   `credenciales.json` (base, dominio, SSL, DEBUG) → `migrate --fake` →
   crear y arrancar el servicio systemd → crear y activar el vhost →
   `certbot --apache` (opcional) → verificar que quedó arriba.
5. Si algo falla a mitad, la tarea muestra un botón **Deshacer** que elimina
   sólo lo que ella creó (carpeta, base, unidad y vhost). Nunca se ejecuta solo.

El botón **Tareas** lista las ejecuciones con su log. Cada alta queda además en
el historial de acciones.

Configuración en `config.json` → `aprovisionamiento`: rutas de los templates y su
base origen, `venv`, rango de puertos, `dominio_base`, instancias modelo para
`.service` y vhost, y `certbot` (`enabled`, `email`). Con `"enabled": false` se
desactiva la creación completa.

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

### Publicar el panel en un dominio (en vez de IP:puerto)

```bash
sudo bash /opt/integrasolucadminvps/deploy/admin_dominio.sh admin.integrasoluc.net \
     --certbot tu-correo@dominio.com
```

Crea el vhost que hace proxy al panel, activa los módulos de Apache, emite el
certificado con certbot, deja el panel escuchando **sólo en 127.0.0.1**,
marca la cookie de sesión como segura, reinicia el servicio y cierra el puerto
en `ufw`. Con `--servidor nginx` usa nginx; con `--mantener-ip` conserva
también el acceso por IP.

Comando de certbot que usa (por si prefieres lanzarlo a mano):

```bash
certbot --apache -d admin.integrasoluc.net --redirect --non-interactive \
        --agree-tos -m tu-correo@dominio.com
```

### Cambiar usuario o clave del panel

```bash
cd /opt/integrasolucadminvps
venv/bin/python run.py --usuario admin --clave "TuNuevaClave"
systemctl restart integrasolucadmin
```

La clave se guarda como hash SHA-256 en `config.json` (permisos 600); nunca en texto plano.

### Actualizar y reiniciar desde /home

En `deploy/` vienen dos scripts pensados para dejarlos en `/home` del servidor,
junto a los demás (`allupdateweb`, `backup.sh`, …):

```bash
cp /opt/integrasolucadminvps/deploy/admin_update.sh  /home/
cp /opt/integrasolucadminvps/deploy/admin_restart.sh /home/
chmod +x /home/admin_update.sh /home/admin_restart.sh
```

- `bash /home/admin_update.sh` — `git pull` (con reintentos), dependencias,
  reinicio del servicio y verificación de que el panel responde en su puerto.
- `bash /home/admin_restart.sh [restart|start|stop|status|logs]` — sin
  argumentos reinicia; también muestra estado, memoria y los últimos logs.

Ambos detectan solos la ruta del proyecto (`/opt/integrasolucadminvps` y otras
habituales) y aceptan `bash /home/admin_update.sh /otra/ruta nombre_servicio`.

También sigue disponible, dentro del proyecto:

```bash
cd /opt/integrasolucadminvps && sudo ./update.sh
```

### Desinstalar el servicio

```bash
sudo ./uninstall.sh
```

---

## Ocultar sistemas del listado (`excluidos.txt`)

En la raíz del proyecto, un archivo `excluidos.txt` con **una sola línea** y los
nombres separados por comas:

```
onepc,elgringo
```

Sirve el nombre del cliente (la carpeta en `/home`) o el del **servicio
systemd**. Acepta espacios, varias líneas y comentarios con `#`. Se relee en
cada refresco, así que no hay que reiniciar el panel, y el pie de la tabla
avisa cuántos sistemas quedaron ocultos.

El archivo está en `.gitignore` (es tuyo, no se sube). Hay un
`excluidos.example.txt` con el formato.

También se administra desde el navegador en **`/excluidos`** (botón *Excluidos*
en la cabecera). Esa página es **el mismo panel completo** —las mismas columnas,
el mismo detalle y las mismas acciones (iniciar, detener, reiniciar, sitio web,
credenciales, API de cédula…)— pero muestra **todas** las instalaciones,
ocultas y visibles, y añade un interruptor por fila para sacarlas o devolverlas
al listado principal. Abajo queda el contenido de `excluidos.txt` editable a
mano. El cambio se refleja al instante y se dispara un refresco.

Los sistemas ocultos se siguen recolectando (por eso la página tiene sus datos);
con `"recolectar_ocultas": false` en `config.json` se omiten del todo si
prefieres ahorrar tiempo en cada refresco.

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
| `apache_dirs`, `nginx_dirs` | Directorios de sitios si no son los de Debian/Ubuntu |
| `excluidos_archivo` | Ruta alternativa del `excluidos.txt` |
| `intervalo_refresco` | Segundos entre refrescos automáticos en segundo plano |
| `medir_logs` | `false` no busca ni suma los archivos de log |
| `acciones` | `enabled`, `servicios`, `apache`, `datos`, `certbot`: permite o bloquea cada tipo de acción |
| `recolectar_ocultas` | `false` no recolecta los sistemas de `excluidos.txt` |
| `credenciales` | `ver`, `editar`, `mostrar_secretos`: controla el acceso a `credenciales.json` |
| `aprovisionamiento` | Creación de instancias: templates, `venv`, puertos, `dominio_base`, modelos y `certbot` |
| `auth` | `username` + `password_hash` (o `password`) y `api_token` opcional |
| `session_cookie_secure` | `true` cuando el panel va detrás de HTTPS (lo pone `admin_dominio.sh`) |

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

> **La primera recolección tarda**: medir el tamaño de cada `media` con `du`,
> consultar todas las bases y verificar las URLs puede llevar varios minutos con
> muchas instancias. La tabla se va llenando conforme cada instancia termina y
> la cabecera muestra el avance ("Recolectando 12 de 37 instancias…"). A partir
> de ahí el tamaño de `media` queda en caché (6 h por defecto) y los refrescos
> son mucho más rápidos.

## API

| Endpoint | Descripción |
|---|---|
| `GET /api/estado` | Estado de todas las instancias + resumen |
| `GET /api/instancia/<cliente\|tipo>` | Detalle de una instancia |
| `POST /api/refrescar` | Fuerza un refresco (`{"solo": "id", "media": true}`) |
| `POST /api/accion` | `{"id": "...", "accion": "reiniciar"}` |
| `GET /api/credenciales/<cliente\|tipo>` | `credenciales.json` con las claves ocultas (`?secretos=1` las revela) |
| `POST /api/credenciales/<cliente\|tipo>` | Guarda el archivo (`{"texto": "{...}"}`) dejando respaldo |
| `GET /api/excluidos`, `POST /api/excluidos` | Lee y guarda la lista de sistemas ocultos |
| `POST /api/excluidos/alternar` | Oculta o muestra una instancia (`{"cliente","servicio","ocultar"}`) |
| `GET /api/certificados` | Certificados de Let's Encrypt con emisión, vencimiento y días |
| `POST /api/certificados/accion` | `pausar`, `reanudar` o `eliminar` un certificado |
| `GET /api/backups`, `POST /api/backups/crear` | Backups: listado y generación (`{"ids"}` o `{"bases"}`) |
| `POST /api/backups/eliminar`, `GET /backups/descargar` | Borrar y descargar un backup |
| `GET /api/bases` | Bases del servidor y cuáles no tiene ninguna instancia |
| `GET /api/diagnostico/vhosts?id=<instancia>` | Qué sitios web leyó el panel y el puntaje de cada candidato |
| `POST /api/certificados/renovar` | Renueva (`{"nombre","forzar","simular"}`) en segundo plano |
| `POST /api/instancia/<id>/api-cedula` | Activa o desactiva la búsqueda por cédula (`{"activar"}`) |
| `GET /api/aprovisionar/opciones` | Datos para el asistente (templates, puerto libre, modelos) |
| `POST /api/aprovisionar/validar` | Valida un alta sin ejecutarla |
| `POST /api/aprovisionar/crear` | Lanza la creación (`{"simular": true}` para el modo simulación) |
| `GET /api/tareas`, `GET /api/tarea/<id>` | Tareas en segundo plano y su log (`?desde=N`) |
| `POST /api/tarea/<id>/deshacer` | Revierte lo que creó una tarea fallida |
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
- El visor de `credenciales.json` puede mostrar contraseñas de base de datos y
  de correo. Si accedes por `http://IP:8600` esos datos viajan sin cifrar: usa
  el panel detrás de Apache con SSL, o desactiva la función con
  `"credenciales": {"ver": false}` (o al menos `"mostrar_secretos": false`).
  Cada vez que alguien revela las claves o guarda el archivo queda registrado
  en `var/acciones.log`.
