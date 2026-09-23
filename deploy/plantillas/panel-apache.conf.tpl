# Panel VPS Integrasoluc - __DOMINIO__
# El panel escucha sólo en 127.0.0.1:__PUERTO__ y sale por este vhost.
<VirtualHost *:80>
    ServerName __DOMINIO__

    ProxyPreserveHost On
    ProxyRequests Off
    RequestHeader set X-Forwarded-Proto "http"

    ProxyPass        / http://127.0.0.1:__PUERTO__/
    ProxyPassReverse / http://127.0.0.1:__PUERTO__/

    ErrorLog ${APACHE_LOG_DIR}/panel-admin-error.log
    CustomLog ${APACHE_LOG_DIR}/panel-admin-access.log combined
</VirtualHost>
