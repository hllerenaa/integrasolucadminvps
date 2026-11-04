<VirtualHost *:80>
    ServerName __DOMINIO__

    ProxyPreserveHost On
    ProxyPass /static/ !
    ProxyPass /media/ !
    ProxyPass / unix:__SOCKET__|http://localhost/
    ProxyPassReverse / unix:__SOCKET__|http://localhost/

    Alias /static/ __RUTA__/static/
    <Directory __RUTA__/static/>
        Require all granted
    </Directory>

    Alias /media/ __RUTA__/media/
    <Directory __RUTA__/media/>
        Require all granted
    </Directory>

    ErrorLog ${APACHE_LOG_DIR}/__CLIENTE___error.log
    CustomLog ${APACHE_LOG_DIR}/__CLIENTE___access.log combined
</VirtualHost>
