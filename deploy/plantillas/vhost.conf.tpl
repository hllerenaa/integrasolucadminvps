<VirtualHost *:80>
    ServerName __DOMINIO__

    ProxyPreserveHost On
    ProxyPass /static/ !
    ProxyPass /media/ !
    ProxyPass / http://127.0.0.1:__PUERTO__/
    ProxyPassReverse / http://127.0.0.1:__PUERTO__/

    Alias /static/ __RUTA__/static/
    Alias /media/ __RUTA__/media/
    <Directory __RUTA__/static>
        Require all granted
    </Directory>
    <Directory __RUTA__/media>
        Require all granted
    </Directory>

    ErrorLog ${APACHE_LOG_DIR}/__CLIENTE__-error.log
    CustomLog ${APACHE_LOG_DIR}/__CLIENTE__-access.log combined
</VirtualHost>
