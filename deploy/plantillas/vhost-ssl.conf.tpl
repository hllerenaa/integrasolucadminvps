<VirtualHost *:80>
    ServerName __DOMINIO__

    RewriteEngine on
    RewriteCond %{SERVER_NAME} =__DOMINIO__
    RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,NE,R=permanent]
</VirtualHost>

<IfModule mod_ssl.c>
<VirtualHost *:443>
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

    SSLCertificateFile /etc/letsencrypt/live/__DOMINIO__/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/__DOMINIO__/privkey.pem
    Include /etc/letsencrypt/options-ssl-apache.conf
</VirtualHost>
</IfModule>
